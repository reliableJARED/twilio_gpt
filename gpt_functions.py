
import json
import requests
import openai
from bs4 import BeautifulSoup
from bs4.element import Comment
import urllib.request
import re
from datetime import timedelta, datetime
import time
import random
import threading
from simple_salesforce import Salesforce
import config#this is a local python file config.py

openai.api_key = config.OPENAI_API_KEY
GOOGLE_SEARCH_API_KEY = config.GOOGLE_SEARCH_API_KEY
SALESFORCE_ACCOUT_EMAIL = config.SALESFORCE_ACCOUT_EMAIL
SALESFORCE_PASSWORD = config.SALESFORCE_PASSWORD
SALESFORCE_CONSUMER_KEY = config.SALESFORCE_CONSUMER_KEY
SALESFORCE_CONSUMER_SECRET = config.SALESFORCE_CONSUMER_SECRET

#descriptions for GPT of what functions it has availible - see gpt documentation on fucntion Calling
GPT_FUNCTIONS = [{
    "name": "gpt_google_search",
		"description": "An internet search using google that returns a snippet of text to answer questions about current events and provide access to real-time information",
		"parameters": {
			"type": "object",
			"properties": {
				"query": {
					"type": "string",
					"description": "accepts a string input to search the internet"
				}
			},
			"required": ["query"]}},
        {"name": "gpt_salesforce_query",
        		"description": "Get database information.  Objects that can be searched: Contact.  Fields that can be used in search criteria: Phone",
        		"parameters": {
        			"type": "object",
        			"properties": {
        				"query": {
        					"type": "string",
        					"description": "An SQL query against allowed Objects using acceptable Fields"
        				}
        			},
        			"required": ["query"]
        		}},
        {"name": "gpt_calendarFreeBusy",
                "description": "Get the next availible time to schedule a call",
                "parameters": {
                			"type": "object",
                			"properties": {
                                "availible_call_time":{
                                "type": "boolean",
                                "description":"returns a day and time that somone from Second Wind Consultants can call back."
                                }
                			},
                		}
                	},
        {"name": "gpt_hangup",
                        "description": "hangup the phone call",
                        "parameters": {
                        			"type": "object",
                        			"properties": {
                                        "hangup":{
                                        "type": "boolean",
                                        "description":"will end the current phone call and conversation."
                                        }
                        			},
                        		}
                        	}]


"""
TODO: integrate a standard response object into function returns
"""
def buildFunctionControlObject(message=None,function_call_sequence_list=[],function_call_name=None,function_call_arguments=None,function_call_result=None):
    """
    This is the format used to store information about function calls so that the Server
    understands what gpt needs, and is doing.
            "message": <string> This is what is spoken to user while function is processing.  If none provided default is a random "i need a bit more time" type message,
            "function_call_sequence_list": <dict> {"name":value,"args":value} This allows more complex functions taking longer than 15 seconds to complete (Twilio response timeout) the ability to loop through sending response back to Twilio while the function completes.
            "function_call_name":<string>name of function that gpt wanted to use based on last user input,
            "function_call_arguments":<string> Agrs sent to the function. At this time GPT only sends string type arguments to functions.  However, it can send JSON or List structures, they are simply encoded as string and need to be converted.  The function definition given to gpt determines what is sent here,
            "function_call_result":<string> Result of the function call.  format as string so gpt can use.  If Dict, JSON or other format is needed encapsulate as string
    """
    return {"message": message,
            "function_call_sequence_list": function_call_sequence_list,
            "function_call_name":function_call_name,
            "function_call_arguments":function_call_arguments,
            "function_call_result":function_call_result}



def round_dt(dt, delta):
    #returns datetime rounded to the nearest 15min, rounds up
    return datetime.min + round((dt - datetime.min) / delta) * delta


def escape_from_string(text):
    regex = re.compile('[^a-zA-Z\d\r\n\t\v :]')
    escaped = regex.sub(" ",text)
    return escaped

def google(response):
    #print(f"parse google search results {response}")
    links = []
    #each Item is a 'result' from google.  There should be 10 total
    for page in response["items"]:
        #collect the links from each result
        links.insert(0,page["link"])
    print(f"google result links for GPT review: {links}")
    return links

def tag_visible(element):
    #HTML elements stripping helper
    if element.parent.name in ['style', 'script', 'head', 'title', 'meta', '[document]']:
        return False
    if isinstance(element, Comment):
        return False
    return True

def text_from_html(body,i=0):
    #print(f"extracting text content from website:thread{i}")
    soup = BeautifulSoup(body, 'html.parser')
    #print(f"have HTML for thread{i}")
    texts = soup.findAll(text=True)
    #print(f"text isolated from HTML for thread{i}")
    visible_texts = filter(tag_visible, texts)
    #print(f"filter visible text done for thread{i}")
    websiteText =  u" ".join(t.strip() for t in visible_texts)
    #print(f"finished website text text extraction on thread{i}")
    return websiteText

def generate_Chatcompletion(websiteText,query,i=0):
    print("*********** CLIP OF WEBSITE TEXT SENT TO GPT ********")
    print(f"{websiteText[:200]}")#show about 50 words
    print("***")
    #remove special chars so it can be sent to GPT
    websiteText_escaped = escape_from_string(websiteText)
    #GPT parms
    reqParams= {
            'user':'Jarvis',
            'temperature': 0.8,
            'n': 1,
            'stop': None,
            'messages':[{"role":"system","content":"The current date and time is "+str(datetime.now().strftime('%Y-%m-%d %I:%M:%S %p'))+". You provide text summaries of websites.  You identify key topics mentioned, people and places mentioned, ideas and concepts discussed. You state the date the content was published. The summary should answer the user query."},{"role":"user","content":websiteText_escaped},{"role":"user","content":f"Using the text from my previous prompt, provide an answer to this query:{query}"}]
        }
    print(f"summarizing webpage results with gpt-3.5-turbo on thread{i}")
    # Generate the completion
    response = openai.ChatCompletion.create(model='gpt-4-1106-preview', **reqParams)
    print(f"Results from GPT thread{i}: {response} \n *********END PAGE REVIEW*************")
    #parse the response, select only the content
    completion = response.choices[0].message.content.strip()
    #send back only the text of the response
    return completion

#https://developers.google.com/custom-search/v1/reference/rest/v1/cse/list
def webpage_result_generator(ConversationObject,arguments):
    try:
        #use a normal user agent header so it doesn't show up as 'python'
        header = { 'User-Agent' : 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36' }

        print(f"Extracting content from site: {arguments['link']}")#Overite
        #Use the FIRST link only for now
        google_results = urllib.request.Request(arguments['link'],headers=header)
        #get the raw HTML
        html = urllib.request.urlopen(google_results).read()
        print(f"{arguments['link']} open")
        #parse out text content from the site
        results = text_from_html(html)
        #clean up any special characters
        results_escaped = escape_from_string(results)
        #use gpt to summarize the text content - HARD CODED clip to 16k characters on input
        gptResponse = generate_Chatcompletion(results_escaped[:16000],arguments['query'])
        #clean up any special characters
        gptResponse_escaped = escape_from_string(gptResponse)

        #add to the collection page summmaries in the LAST function in the sequence list, which will be the final summary
        lastElement = len(ConversationObject['function_control_object']['function_call_sequence_list'])
        ConversationObject['function_control_object']['function_call_sequence_list'][lastElement-1]['args'] += gptResponse_escaped
        ConversationObject['function_control_object']['function_call_result'] = gptResponse_escaped
        #make the msg that will be given to user.  if this is the last run of this function don't say '0 seconds left ' say almost done
        msg = arguments['timeLeft']
        if msg == 0:
            msg = "almost done"
        else:
            msg = f"{arguments['timeLeft']} seconds left"
        ConversationObject['function_control_object']['message'] = msg

    except:
        print("there was an error in webpage_result_generator")
        lastElement = len(ConversationObject['function_control_object']['function_call_sequence_list'])
        #add blank data for this website result
        ConversationObject['function_control_object']['function_call_sequence_list'][lastElement-1]['args'] += " "
        ConversationObject['function_control_object']['function_call_result'] = "Faild to review page"
        ConversationObject['function_control_object']['message'] = "couldn't read that page"

    return ConversationObject

def gpt_google_search(ConversationObject,arguments):
    #create the result object to be used with the response, set some of the parameters
    #result = buildFunctionControlObject(function_call_name=gpt_google_search.__name__,function_call_arguments=query,use_function_response_manager=True)
    """ ENTRY for GPT - this is a triggering fuction so it starts with gpt_ it is in the 'name' field of an object in GPT_FUNCTIONS  """

    print(f"gpt_google_search input: {arguments}")
    query = json.loads(arguments)
    #custom search Engine
    #https://programmablesearchengine.google.com/controlpanel/overview?cx=30bb88a8fbc504a7e
    '''
    NOTE: the search engin is restricted to results via 'num' parameter
    '''
    search_result_count = 5
    url = "https://www.googleapis.com/customsearch/v1"

    params = {
        "key": GOOGLE_SEARCH_API_KEY,
        "cx": "30bb88a8fbc504a7e",
        "gl":"us",
        "lr":"lang_en",
        "num":str(search_result_count),
        "q": query['query']
    }
    response = requests.get(url, params=params).json()
    #run google search on
    relaventLinks = google(response)

    print(relaventLinks)

    ConversationObject['function_control_object']['function_call_result'] = relaventLinks
    #prep the function call sequence list that will be use by this main gpt_ function
    ConversationObject['function_control_object']['function_call_sequence_list'] = [None] * len(relaventLinks)

    #it takes about 15 seconds per reviewed page.  Create a total time needed list that is used to update user and map it to the web page results to create a countdown [60,45,30...]
    totalTimeNeeded = list(range(0,len(relaventLinks)*15,15))[::-1]

    #prepare the function sequence list so that each website review is looped
    for i in range(len(relaventLinks)):
        print(f"put relaventLinks[{i}]:{relaventLinks[i]} in function_call_sequence_list")
        ConversationObject['function_control_object']['function_call_sequence_list'][i] = {"name":"webpage_result_generator","args":{"link":relaventLinks[i],"query":query['query'],"timeLeft":totalTimeNeeded[i]}}

    #add the final function which will summarize the results. Note that 'args' will be appended in webpage_result_generator. Eventually it will be the text from each website summary
    ConversationObject['function_control_object']['function_call_sequence_list'].append({"name":"summarize_webpage_review","args":" "})

    ConversationObject['function_control_object']['message'] = "I'll need at least one minute to search and review the internet for that"
    return ConversationObject

def summarize_webpage_review(ConversationObject,arguments):
    print("========================")
    print(f"RESULTS OF SEARCH TO BE SUMMARIZED BY GPT: {arguments}")
    #{"name":"summarize_webpage_review","args":" ","query":query['query']}
    print("========================")
    #summarize all the results
    Q = json.loads(ConversationObject['function_args'])['query']
    #used so system knows current date
    now = str(datetime.now().strftime('%Y-%m-%d %I:%M:%S %p'))
    GPTParams= {
            'user':'Jarvis',
            'temperature': 0.6,
            'n': 1,
            'stop': None,
            'messages':[{"role":"system","content":f"You analyze internet search results. You prioritize current information when applicable. The current date and time is {now}"},{"role":"user","content":f"The search query: {Q} was used to generate the search RESULTS below from multiple websites. Aggragate the content into a single result that can be used to answer the query {Q}. Retain and include dates. \n Here are website summaries from the pages reviewed:\n ###{arguments}###"}]
        }
    response = openai.ChatCompletion.create(model='gpt-4-1106-preview', **GPTParams)
    completion = response.choices[0].message.content.strip()
    #print(f"SUMMARY OF RESULTS: {completion}")
    escape_completion = escape_from_string(completion)
    ConversationObject['function_control_object']['function_call_result'] = escape_completion
    ConversationObject['function_control_object']['message'] = "Here's what I found"
    return ConversationObject

def gpt_calendarFreeBusy(ConversationObject,arguments):
    #create the result object to be used with the response, set some of the parameters
    #result = buildFunctionControlObject(function_call_name=gpt_calendarFreeBusy.__name__,function_call_arguments=boolean)
    #MAKE scenario - Twilio get Calendar Freebusy Info
    now = datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
    query = {
            "timeMin": now,
            "email": "jnugent@secondwindconsultants.com"}

    url = "https://hook.us1.make.com/5ofx1hhnp5lsw74bkkaw10niupxxptx7"
    params = {"key": 'placeHolder_notUsed'}
    headers = { 'Content-Type':'application/json'}
    response = requests.get(url,json=query,params=params,headers=headers)
    delta = timedelta(minutes=15)
    print(f"Response from Google Calendar {response}")
    try:
        year = int(response.json()['year'])
        month = int(response.json()['month'])
        day = int(response.json()['day'])
        hour = int(response.json()['hour'])
        minute = int(response.json()['minute'])
        second = 0
        dt = datetime(year, month, day, hour, minute, second)
        freeTime = round_dt(dt,delta)
        print(f"freetime:{freeTime}")
        #format as Weekday, Month Date at Hour:Min AM/PM
        ConversationObject['function_control_object']['function_call_result'] = str(freeTime.strftime('%A, %B %d, at %I:%M %p'))
        ConversationObject['function_control_object']['message'] = "calendar check done"
        return ConversationObject
    except:
        ConversationObject['function_control_object']['function_call_result'] = "No Availible Time Found"
        ConversationObject['function_control_object']['message'] = "calendar check done"
        return ConversationObject

def gpt_salesforce_query(ConversationObject,arguments):
    #active_function = ConversationObject['function_control_object']['function_call_sequence_list'].pop(0)
    query = json.loads(arguments)

    print(f"Salesforce Query: {query}")
    try:
        sf = Salesforce(username=SALESFORCE_ACCOUT_EMAIL, password=SALESFORCE_PASSWORD, consumer_key=SALESFORCE_CONSUMER_KEY, consumer_secret=SALESFORCE_CONSUMER_SECRET, client_id="twilio")
        #result = sf.query(f"SELECT Id, LastName, FirstName, Email, Phone FROM Contact WHERE (Phone = '{phoneNumber[2:]}') OR (MobilePhone = '{phoneNumber[2:]}')")
        sf_result = sf.query(query)
        #SF had a match
        if sf_result['totalSize'] >= 1:
            ConversationObject['function_control_object']['function_call_result'] = sf_result['records'][0]
            return ConversationObject
        #SF had no match
        else:
            ConversationObject['function_control_object']['function_call_result'] = "No results found"
            return ConversationObject
    except:
        ConversationObject['function_control_object']['function_call_result'] = "Error malformed query or requested fields and objects don't exist"
        return ConversationObject

def gpt_hangup(ConversationObject,arguments):
    #the first item in the list of functions is always removed, so it can be worked on and to update the fact that it's done and shouldn't be in the list anymore
    #active_function = ConversationObject['function_control_object']['function_call_sequence_list'].pop(0)
    ConversationObject['function_control_object']['function_call_result'] = "hangup the call"
    ConversationObject['function_control_object']['message'] = "It was nice speaking with you, good bye."
    return ConversationObject
