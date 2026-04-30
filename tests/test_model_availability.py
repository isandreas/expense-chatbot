import os

from dotenv import load_dotenv
from google import genai

load_dotenv()
c = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
r = c.models.generate_content(model='gemini-2.0-flash', contents='Say hi in 3 words')
print('Response:', r.text)