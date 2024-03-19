#https://twilio-401222.uc.r.appspot.com/
'''Resources'''
#https://www.twilio.com/docs/voice/tutorials/consume-real-time-media-stream-using-websockets-python-and-flask
#https://www.twilio.com/docs/voice/tutorials/how-to-respond-to-incoming-phone-calls/python
from flask import Flask, request, make_response, session
from datetime import datetime, timedelta
import openai
import json
import random
import re
import gpt_functions as gptf #separate file in this directory that stores the GPT functions
from simple_salesforce import Salesforce
from pympler import asizeof #used in testing cookie absolute byte size

app = Flask(__name__)
#need this to use the session storage
app.config.from_pyfile("config.py")
openai.api_key = app.config['OPENAI_API_KEY']
SALESFORCE_ACCOUT_EMAIL = app.config['SALESFORCE_ACCOUT_EMAIL']
SALESFORCE_PASSWORD = app.config['SALESFORCE_PASSWORD']
SALESFORCE_CONSUMER_KEY = app.config['SALESFORCE_CONSUMER_KEY']
SALESFORCE_CONSUMER_SECRET = app.config['SALESFORCE_CONSUMER_SECRET']
APP_ROOT_URL = app.config['APP_ROOT_URL']

"""TODO:
Have voice and language set in the cookies.  When someone calls in, randomly assign a voice.  Can also setup the option to 'speak to somone else' or change language
"""

SWC_System = "You work in a call center at Second Wind Consultants.  The phone number is (413) 584-2581,\
email is info@secondwindconsultants.com. Your name is Link.\
You are responsible for educating prospective leads on the services provided by Second Wind Consultants.\
It is important to know that You, Link, interact with users on a voice platform.  Users do not input text.\
your outputs are formatted with SSML tags so that they may be output through a speech to text system. <s> should be used for periods and <break> and <prosody> should be used for emphasis.\
all tags should have corresponding terminating tags, like </break> </prosody> </s>. Do not start and end with <speak>, this tag is already included when playing output.\
You should end response with a question that continues the conversation.\
Second Wind Consultants (SWC) provides consulting services to distressed businesses and helps them resolve issues with their creditors.\
SWC specializes in using Article 9 secured party sales, or A9 sales to reorganize businesses.\
Additionally SWC settles merchant cash advance (MCA) debt, SBA debt, traditonal bank debt and other similar obligations.\
SWC provides a way to reorganize a business, or resolve creditor disputes without filing for bankruptcy.\
Pricing is best discussed with a person.\
If questions have been answered, ask questions about the user.  Only ask one question at a time.  How much debt does their business have?  What type of creditors? What type of business is it? Are they already in default?\
ALL of your answers use 25 words or less and should be related to Second Wind content.\
Let users know they should respond to you with short answers because you can not process long inputs.\
If an input does not seem to make sense it may be that the user microphone stopped recording to early, ask them to repeat what they said.\
Your goal is to encourge the user to setup a time to have SWC call them to chat more. Get basic contact information.  Name and phone number.\
All responses should end with a follow up question to the user.  Either asking if they have more questions on the topic, or for contact information"

GPT_SYSTEM_PROMPT = "You interact with users on a voice platform.  Users do not input text.  your outputs are formatted with SSML tags so that they may be output through a speech to text system. <s> should be used for periods and <break> and <prosody> should be used for emphasis. all tags should have corresponding terminating tags, like </break> </prosody> </s>. Do not start and end with <speak>, this tag is already included when playing output. You should end response with a question that continues the conversation."
#GPT_SYSTEM_PROMPT = SWC_System

GPT_FUNCTIONS = gptf.GPT_FUNCTIONS

"""
https://www.twilio.com/docs/messaging/guides/webhook-request
TWILIO Request parameters

The GET request will have the following request.values
Called:	"+18559451914"
ToState:	""
CallerCountry:	"US"
Direction:	"inbound"
CallerState:	"MA"
ToZip:	""
CallSid:	"CallSid"
To:	"+18559451914"
CallerZip:	"01118"
ToCountry:	"US"
ApiVersion:	"2010-04-01"
CalledZip:	""
CallStatus:	"ringing" - "in-progress" - "completed" -> in-progress is what it will be for everything after the initial GET
CalledCity:	""
From:	"+14136275560"
AccountSid:	"AccountSid"
CalledCountry:	"US"
CallerCity:	"SPRINGFIELD (EL)"
Caller:	"+14136275560"
FromCountry:	"US"
ToCity:	""
FromCity:	"SPRINGFIELD (EL)"
CalledState:	""
FromZip:	"01118"
FromState:	"MA"

Additionally - POSTs with input from user will have
SpeechResult: "text result from audio Speech-to-text"
 """

#initial entry point from Twilio
@app.route("/SpeechResult_get",methods=['GET', 'POST'])
def SpeechResult_get():
    #unique id for this conversation
    CALLSID = request.values['CallSid']

    #the phone phoneNumber
    phoneNumber = request.values['From']

    #check if the phone number is in the database with a Contact
    firstName = salesforce_getFirstName_from_Phone(phoneNumber)

    #use default message that is stated when caller first calls
    OPENING_STATEMENT = "Hello, my name is Link. I'm an AI service agent.  You can talk to me like a person, using short responses. What can I help you with?"
    #customize the message if they were found in the database
    if firstName:
        OPENING_STATEMENT = f"Hello {firstName}. my name is Link. I'm an AI service agent.  You can talk to me like a person, using short responses. What can I help you with?"
    #build the response xml, redirect to SpeechResult_acknowledge
    xml = twilio_xml_maker(speak=OPENING_STATEMENT,url=APP_ROOT_URL+"SpeechResult_acknowledge")
    response = buildXml_200response(xml)

    #create the storage object for the conversation
    CONVERSATION = buildConversationObject(SysPrompt=GPT_SYSTEM_PROMPT,SysIntro=OPENING_STATEMENT)

    #create a record of the conversation in the database
    success = conversation_memory_create(data=CONVERSATION,CallSid=CALLSID,timestamp=datetime.now())

    #send back to Twilio
    return response

#use this route when sending back a quick response that acknowledges speech to text result was received
@app.route("/SpeechResult_acknowledge", methods=['GET', 'POST'])
def SpeechResult_acknowledge():
    '''
    This endpoint is used to confirm with user that their input was received, and package that input into the conversation database, which will be referenced later and processed in a downstream post request to 'SpeechResult_process'
    '''
    #first load the current conversation history, using the unique CallSid to look it up in the database of conversations
    CALLSID = request.values['CallSid']
    CONVERSATION = conversation_memory_get(CallSid=CALLSID)

    #escape special json characters from user input
    userInput = esc_json_control_chars(request.values['SpeechResult'])
    print(f"***SpeechResult_acknowledge*** User Input was: {userInput}")

    #add the input to the conversation data this will be processed by GPT on the next POST request
    CONVERSATION['gpt_conversation_messages'].append({"role":"user","content":userInput})

    #update database, note the 'acknowledgment' xml isn't actually part of the conversation that's why db is updated now.  'acknowledgment' is just letting user know 'input received', but chatGPT does not need that to be part of the conversation
    success = conversation_memory_update(data=CONVERSATION,CallSid=CALLSID)

    #get a quick acknowledgment phrase like 'ok, let me check on that'
    messageToSpeak = acknowledgment_phrase()

    #setting type to 'acknowledgment' means Twilio will not try to capture new audio
    xml = twilio_xml_maker(type='acknowledgment',speak=messageToSpeak,url=APP_ROOT_URL+"SpeechResult_process")
    response = buildXml_200response(xml)

    #send back to Twilio
    return response

#this route is used to get a response to GPT from the user input
@app.route("/SpeechResult_process", methods=['GET', 'POST'])
def SpeechResult_process():
    '''
    This endpoint is used to process the last user input in the conversation database.  The result is either a direct answer from GPT, or a function call if GPT determines it needs to call a function to produce an answer to the input, like googling something
    '''
    #load the conversation from the database, which is referenced by the unique CallSid
    CALLSID = request.values['CallSid']
    CONVERSATION = conversation_memory_get(CallSid=CALLSID)

    #send the conversation, which has user input as last entry, to GPT for a response.  will get back the full conversation and the gpt msg response.  note the response could be a request to call a function.
    results = generate_Chatcompletion(CONVERSATION)

    #update the conversation with the response from gpt. could combine this with above
    CONVERSATION = results

    #the response from GPT says a function call is required, send response to user for more time.  This will also loop the request back as a new POST to functionCall_request endpoint
    if CONVERSATION['function_call']:
        xml = twilio_xml_maker(type='acknowledgment',speak=CONVERSATION['function_control_object']['message'],url=APP_ROOT_URL+"functionCall_request")
        response = buildXml_200response(xml)

        #update the database prior to returning
        success = conversation_memory_update(data=CONVERSATION,CallSid=CALLSID)

        #return to Twilio with an acknowledgment, note that function calls will have a message that is slightly different than acknowledgment_phrase()
        return response

    #build the response to the user from the GPT output, and Loop back to acknowledge since we will be getting new input
    xml = twilio_xml_maker(speak=CONVERSATION['message'],url=APP_ROOT_URL+"SpeechResult_acknowledge")
    response = buildXml_200response(xml)

    #update the database prior to returning
    success = conversation_memory_update(data=CONVERSATION,CallSid=CALLSID)

    #send back to Twilio, final answer for the user
    return response

#this route is used to process function call requests from GPT
@app.route("/functionCall_request", methods=['GET', 'POST'])
def functionCall_request():
    #load the conversation from the database, which is referenced by the unique CallSid
    CALLSID = request.values['CallSid']
    CONVERSATION = conversation_memory_get(CallSid=CALLSID)

    #process the function call
    results = process_Chatcompletion_function_call(CONVERSATION)

    #update what the function results were
    CONVERSATION = results

    #placeholder for XML
    xml = None

    if CONVERSATION['function_control_object']['function_call_result'] == "hangup the call":
        #this means the system wants to end the call
        xml = twilio_xml_maker(type='hangup',speak=CONVERSATION['function_control_object']['message'])
    elif CONVERSATION['function_call']:
        #this means the system wants keep using other sub functions
        xml = twilio_xml_maker(type='acknowledgment',speak=CONVERSATION['function_control_object']['message'],url=APP_ROOT_URL+"functionCall_request")
    else:
        #conversation flow continues by sending a 'i need more time' message now that function data is back and ready to go to GPT to process and provide output.
        #we can't send the output straight back, since it's raw fucntion output and we need gpt to process it.  that's why this is done like this
        xml = twilio_xml_maker(type='acknowledgment',speak=CONVERSATION['message'],url=APP_ROOT_URL+"SpeechResult_process")


    #prepare response
    response = buildXml_200response(xml)

    #update the database prior to returning
    success = conversation_memory_update(data=CONVERSATION,CallSid=CALLSID)

    #send back to Twilio
    return response

def buildXml_200response(xml,cookies=False):
    #used to build responses in XML for twilio that have appropriate headers and cookies
    #twilio has this in their python library, along with XML maker however there was no way to change cookies so it's implemented here to provide that ability
    resp = make_response(xml)
    resp.status_code = 200
    resp.headers['Content-type'] = "application/xml"
    if cookies:
        expires = datetime.utcnow() + timedelta(hours=2)
        for c in cookies:
            resp.set_cookie(c['name'],value=c['value'],expires=expires)
    return resp

def buildConversationObject(SysPrompt="You are AI",SysIntro="Hello I am Jarvis"):
    """
    STRUCTURE
    Note about the structure for fuctions.  Function calling is nested.  There is an outer function call, that has the name of the function and its arguments.  This is provided by ChatGPT.  A function call may actually consist of multiple functions being
    used to provide the desired output.  ChatGPT is 'unaware' of this, but the control logic needs to track this.  This feature is added exclusivly because Twilio requires responses in 15seconds or less.  A function call that is actually a chain could take longer, so it gets
    broken into parts with each part sending a quick response to Twilio as an acknowledgement like "still processing".  This helps to ensure that a function that could take 30 seconds (like google result reviews or calendar free time checks) do not have any single Link
    in the chain exceed 15 seconds.  In the end ChatGPT is finally returned the end result at the end of the chain, and it uses that information to construct its response to the user.
    {'gpt_conversation_messages':<list> list of json objects formated to meet OpenAI GPT ChatCompletion API format,
            'message': <string> Text that will be spoken to the user next,
            'function_call':<boolean> flag used to determine if GPT needs to do a function call to respond to user input,
            'function_name':<string> name of function in gpt_functions.py that GPT wants to use,
            'function_control_object':<Dict> manages the function use, or chain of functions being used
                        {"message": <string> Text that will be spoken to the user next,
                        "function_call_sequence_list": <list> of dicts containing function names to be used and their arguments.  they are called in index order,
                        "function_call_name":<string> name of next fuction to be called,
                        "function_call_arguments": <string> args for that funciton,
                        "function_call_result":None}
            'function_args':<string> This is direct output from GPT, it determines what the args should be based on the 'parameters' and 'description' elements of GPT_FUNCTIONS in gpt_functions.py,
            'usage':<json> prompt_tokens,completion_tokens, and total_tokens consumed by LAST call.  These fields are not cummulative.
            'total_usage': <json> prompt_tokens,completion_tokens, and total_tokens consumed ALL calls.  cummulative of 'usage'.}
    """
    #constructor for the primary 'conversation object used through the whole conversation with user
    return {'gpt_conversation_messages':[{"role":"system","content":SysPrompt},{"role":"assistant","content":SysIntro}],
            'message':None,
            'function_call':False,
            'function_name':"None",
            'function_control_object':None,
            'function_args':None,
            'usage':{'prompt_tokens':None,'completion_tokens':None,'total_tokens':None},
            'total_usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}}

def generate_Chatcompletion(ConversationObject,gpt_model="gpt-4-1106-preview"):
        #'gpt-4, gpt-3.5-turbo-16k-0613'#'gpt-4-0613'##'gpt-4-0613'#'gpt-3.5-turbo-16k-0613'
        #print(f"Input to GPT message parameter: {ConversationObject}")
        """CONSIDER - add a time stamp update so it always knows the current time
        CONVERSATION_MEMORY[0] = {"role": "system", "content": GPT_SYSTEM_PROMPT + ".  The current time is " + str(datetime.datetime.now().strftime('%Y-%m-%d %I:%M:%S %p'))}
        """

        # Set the OpenAI completion parameters
        #https://platform.openai.com/docs/api-reference/completions/create
        reqParams= {
            'user':"Jarvis",
            'temperature': 0.9,
            'n': 1,
            'stop': None,
            'messages':ConversationObject['gpt_conversation_messages'],
            'functions':GPT_FUNCTIONS,
            'function_call':"auto"
        }

        '''
        # CONSIDER: gpt4 is 10x the cost and 3x slower than 3.5.
        '''
        # Generate the completion
        #https://stackoverflow.com/questions/1483429/how-do-i-print-an-exception-in-python
        try:
            response = openai.ChatCompletion.create(model=gpt_model, **reqParams)
            #print(response)
        except Error:
            print(Error)

        #Check if GPT wants to use a function to help answer the input:
        if response.choices[0]["finish_reason"] == "function_call":
            print("GPT IS CALLING A FUNCTION")
            #package output as having used a function call, prep the return for user.  Note this will NOT actually call the function yet
            output = package_Chatcompletion_function_call(resp=response,output=ConversationObject)
            return output

        #GPT is not using a function, it simply generated a response to the input
        elif response.choices[0]["finish_reason"] != "function_call":
            print("GPT HAS AN ANSWER")
            #process the output from GPT so a response to user can be generated
            output = process_Chatcompletion_message(resp=response,output=ConversationObject)
            return output

def process_Chatcompletion_message(resp=None,output=None):
    """ DATA INPUT STRUCTURE
    output = {
            'gpt_conversation_messages':[{"role": "assistant", "content":completion},{}...],
            'message':None,
            'function_call':False,
            'usage':{'prompt_tokens':None,'completion_tokens':None,'total_tokens':None}
        }
    resp = {
            "id": "chatcmpl-88HHPX3wwnDx5EoDCxOvlITOaeJco",
            "object": "chat.completion",
            "created": 1696983739,
            "model": "gpt-3.5-turbo-0613",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Ok, Good question. Here is an answer"
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 83,
                "completion_tokens": 19,
                "total_tokens": 102
            }
        }
    """
    completion = resp.choices[0].message.content.strip()
    output['message'] = completion
    output['usage'] = resp.usage
    output['total_usage']['prompt_tokens'] = int(resp.usage['prompt_tokens']) + output['total_usage']['prompt_tokens']
    output['total_usage']['completion_tokens'] = int(resp.usage['completion_tokens']) + output['total_usage']['prompt_tokens']
    output['total_usage']['total_tokens'] = int(resp.usage['total_tokens']) + output['total_usage']['total_tokens']
    output['gpt_conversation_messages'].append({"role": "assistant", "content":completion})
    output['function_call'] = False
    return output

def package_Chatcompletion_function_call(resp=None,output=None):
    func_name = resp.choices[0]["message"]["function_call"]["name"]
    func_args = resp.choices[0]["message"]["function_call"]["arguments"]
    #message to user indicate still working
    default_message = funciton_name_phrase(func_name)#need_more_time_phrase()
    #first create a function control object
    output['function_control_object'] = gptf.buildFunctionControlObject(function_call_name=func_name,function_call_arguments=func_args,message=default_message,function_call_sequence_list=[{"name":func_name,"args":func_args}])

    #set the name of the function GPT wants to call
    output['function_name'] = func_name

    #arguments GPT thinks it should send to the function
    output['function_args'] = func_args

    #how many tokens were used
    output['usage'] = resp.usage
    #flag that this output contains a function call that needs to be processed - Function not actually called here
    output['function_call'] = True
    return output


def process_Chatcompletion_function_call(input=None):
    """
    there is a standard response object to function returns - see gpt_functions.py it's contained under the ['function_control_object'] property of input

    "message": <string> This is what is spoken to user while function is processing.  It's typically a "i need a bit more time" type message,
    "function_call_sequence_list": <list> This allows more complex functions taking longer than 15 seconds to complete (Twilio response timeout) the ability to be broken up and loop through sending response back to Twilio while the function completes.
    "function_call_name":<string>name of function that gpt wanted to use based on last user input,
    "function_call_arguments":<multi string> Agrs sent to the function. At this time GPT only sends string type arguments to functions.  However, it can send JSON or List structures, they are simply encoded as string and need to be converted.  The function definition given to gpt determines what is sent here,
    "function_call_result":<multi string> Result of the function call.  format as string so gpt can use.  If Dict, JSON or other format is needed encapsulate as string
    """
    #unpack the output argument so that the requested function can be called with the supplied arguments
    #name of the function GPT wants to call. This is doesn't not have to be the same as the 'active' function which the function being used
    func_name = input['function_name']
    #arguments gpt wanted to send to the function
    func_args_json_str = input['function_args']
    #convert arg string to a json
    #func_args = json.loads(func_args_json_str)

    print(f"function_call_sequence_list: {input['function_control_object']['function_call_sequence_list']}")

    #the function_call_sequence_list is a list of dicts {name,ars} that need to be called.  Each element is removed as it is worked on.  When 0, it's finally returned to gpt so it can use in its answer.  Most function calls are just 1
    active_function_name = input['function_control_object']['function_call_sequence_list'][0]['name']
    active_function_args = input['function_control_object']['function_call_sequence_list'][0]['args']

    #now remove this function from the function call function_call_sequence_list
    input['function_control_object']['function_call_sequence_list'].pop(0)

    #get the function using string name from the gpt_functions.py file so it can be used to execute
    #gpt_func_called = getattr(gptf,func_name)
    active_func_called = getattr(gptf,active_function_name)

    #execute the function using args from GPT
    #func_output = gpt_func_called(func_args)
    #pass the entire conversation object with the function arguments
    output = active_func_called(input,active_function_args)

    #set input as output, since input has the desired output structure. this is really just to help a human read it so that it goes from input->output
    #output = input

    print(f"Function Control Object: {output['function_control_object']}")

    #message to indicate still workinig if none was provided
    if output['function_control_object']['message'] == None:
        output['function_control_object']['message'] = need_more_time_phrase()

    #this means that the function requires a list of functions and will take awhile to complete and it needs to loop responses back to Twilio to prevent the 15seconds response timeout limit from being reached
    if len(output['function_control_object']['function_call_sequence_list']) > 0:
        """
        This is where looping through multi levels of function calls kicks in
        """
        return output

    #add the fact a function was called to the message log of GPT - see gpt function calling documents for more details
    output['gpt_conversation_messages'].append({"role": "assistant", "content": "null","function_call":{"name":func_name,"arguments":func_args_json_str}})

    #add the output from that function
    output['gpt_conversation_messages'].append({"role":"function","name":func_name,"content":output['function_control_object']['function_call_result']})

    #flag back to false since now we have our data from the function call and we no longer need to process a function call
    output['function_call'] = False
    #clear the function control object
    output['function_control_object']['function_call_sequence_list'] = []
    #set the last function_call_sequence_list message to the main message in the ConversationObject since we are now done with function calling
    output['message'] = output['function_control_object']['message']

    return output

def twilio_xml_maker(type='gather',voice='Google.en-GB-Standard-B',language='en-GB',url=APP_ROOT_URL,method='POST',timeout=1,speak=""):
    #type: gather, acknowledgment
        #gather creates a response that will collect new audio
        #acknowledgment will create a quick response to say, with a redirect back - like 'ok i'll check'. it does NOT wait to collect new audio
    #speak: what should go in the say statement since ALL of these have a say
    #voice: the type of voice to be used default 'Google.en-GB-Standard-B'#https://www.twilio.com/docs/voice/twiml/say/text-speech#available-voices-and-languages
    #language: language code, default 'en-GB'
    #method: post or get for the request
    #url: where the request should go
    #timeout: how many seconds of silience befor stop recording and submit audio

    gather = f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\
    <Response>\
    <Gather input=\"speech\" speechTimeout=\"{timeout}\" enhanced=\"false\" action=\"{url}\" method=\"POST\" speechModel=\"phone_call\">\
        <Say voice=\"{voice}\" language=\"{language}\">{speak}</Say>\
        </Gather>\
        <Say voice=\"{voice}\" language=\"{language}\">Sounds like you dont want to chat. goodbye.</Say>\
    </Response>"

    acknowledgment = f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\
    <Response>\
        <Say voice=\"{voice}\" language=\"{language}\">{speak}</Say>\
        <Redirect method=\"{method}\">{url}</Redirect>\
    </Response>"

    hangup = f"<Response>\
                <Say voice=\"{voice}\" language=\"{language}\">{speak}</Say>\
                <Hangup/>\
            </Response>"

    if type == 'hangup':
        return hangup
    if type == 'gather':
        return gather
    elif type == 'acknowledgment':
        return acknowledgment

def esc_json_control_chars(text):
    #escape json control characters from the input
    escaped = text.replace('"', '\"').replace('\n', '\\n').replace('\b', '\\b').replace('\f', '\\f').replace('\r', '\\r').replace('\t', '\\t')
    return escaped

def acknowledgment_phrase():
    #used to produce a quick acknowledgment like 'ok', or 'just a momment' after an input
    sound = ["just a moment","one second","give me a minute","hang on","just a second"]
    return random.choice(sound)

def need_more_time_phrase():
    #used to produce an i need more time sound
    sound = ["I need a bit more time","just a few more moments","give me a bit longer","Almost there"]
    return random.choice(sound)
def funciton_name_phrase(name):
    if name == "gpt_google_search":
        return "I'll need to google that"
    if name == "gpt_calendarFreeBusy":
        return "Let me check the calendar"
    else:
        return need_more_time_phrase()

def processing_phrase():
    #used to produce an i need more time sound
    sound = ["I am still processing","re","give me a bit longer","Almost there"]
    return random.choice(sound)

def salesforce_getFirstName_from_Phone(phoneNumber):
    sf = Salesforce(username=SALESFORCE_ACCOUT_EMAIL, password=SALESFORCE_PASSWORD, consumer_key=SALESFORCE_CONSUMER_KEY, consumer_secret=SALESFORCE_CONSUMER_SECRET, client_id="twilio")
    #strip to characters from phoneNumber because twilio sends a +1 at the front.  Use %like wild card incase the salesforce number has the 1
    result = sf.query(f"SELECT Id, LastName, FirstName, Email, Phone FROM Contact WHERE (Phone LIKE '%{phoneNumber[2:]}%') OR (MobilePhone LIKE '%{phoneNumber[2:]}%')")
    if result['totalSize'] >= 1:
        return result['records'][0]['FirstName']
    else:
        return False

def conversation_memory_get(CallSid=None):
    #gets a conversation history from the database
    """SUDO below.  This is not actually accessing a db, but it's quick code
    that is supposed to simulate it until an acutal db implementation is put in place
    """
    sf = Salesforce(username=SALESFORCE_ACCOUT_EMAIL, password=SALESFORCE_PASSWORD, consumer_key=SALESFORCE_CONSUMER_KEY, consumer_secret=SALESFORCE_CONSUMER_SECRET, client_id="twilio")

    #Reference_Notes__c is 32000 char text field on contacts
    data = sf.Contact.get('0036Q000038jXVCQA2')['Reference_Notes__c']
    #unstringify the json data
    conversation_memory = json.loads(data)
    return conversation_memory

def conversation_memory_update(data=None,CallSid=None,timestamp=None):
    sf = Salesforce(username=SALESFORCE_ACCOUT_EMAIL, password=SALESFORCE_PASSWORD, consumer_key=SALESFORCE_CONSUMER_KEY, consumer_secret=SALESFORCE_CONSUMER_SECRET, client_id="twilio")
    #stringify
    package = json.dumps(data)
    result = sf.Contact.update('0036Q000038jXVCQA2',{'Reference_Notes__c':package})
    return True

def conversation_memory_create(data=None,CallSid=None,timestamp=None):
    """placeHolder this will be needed to create new db records at some point"""
    success = conversation_memory_update(data)
    return success

if __name__ == "__main__":
    app.run()
