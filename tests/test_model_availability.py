import os

from dotenv import load_dotenv
from google import genai


def main():
    load_dotenv()
    c = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
    r = c.models.generate_content(
        model=os.getenv('GEMINI_MODEL', 'gemini-3.6-flash'),
        contents='Say hi in 3 words',
    )
    print('Response:', r.text)


if __name__ == '__main__':
    main()