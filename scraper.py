import requests 
from bs4 import BeautifulSoup 
from pyrogram import Client, filters
import os

# Load credentials from environment variables
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

bot = Client(
    'my_bot',
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

@bot.on_message(filters.private & filters.command('start'))
def start(bot, msg):
    msg.reply(f'Hello {msg.from_user.first_name}! I am a webpage source code downloader bot. Just send me a link.')


	
@bot.on_message(filters.private & filters.regex("http"))
def scrap(bot, msg):
    url = msg.text 
    request = requests.get(url)
    soup = BeautifulSoup(request.content, 'html.parser')
    parse = open('source-code.txt', 'w', encoding="utf-8")
    we = parse.write(soup.prettify())
    parse.close()
    msg.reply_document("source-code.txt")

       
@bot.on_message(filters.private & filters.text)
def show(bot, msg):
    msg.reply(text="**Your link must start from http like:\nhttps://www.google.com\n\nFor more feel free to contact the** [Developer](https://t.me/e_phador)", disable_web_page_preview=True, quote=True)
	    
    
bot.run()