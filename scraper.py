import requests 
from bs4 import BeautifulSoup 
from pyrogram import Client ,filters
import os

bot = Client(
'my_bot',
api_id =18802415,
api_hash = 'a8993f96404fd9a67de867586b3ddc92',
bot_token = '5772504388:AAEX0vjm-rx1sYdLU_qLpIS_RMqSvi5KANg')

@bot.on_message(filters.private & filters.command ('start'))
def start (bot , msg):
        msg.reply (  f'hello Sir{msg.from_user.first_name} i am webpage source code downloader bot just send me a link')


	
@bot.on_message(filters.private & filters.regex("http"))
def scrap (bot,msg):
    url = msg.text 
    request =requests.get(url)
    soup = BeautifulSoup(request.content , 'html.parser')
    parse = open( 'source-code.txt','w' ,encoding="utf-8")
    we = parse.write(soup.prettify())
    parse.close
    msg.reply_document("source-code.txt")

       
@bot.on_message(filters.private & filters.text)
def show (bot , msg ):
	msg.reply (text = " **your link must start from ◇◇♤◇◇http like as \n https://www.google.com \n \n \n for more feel free to contact the** : [Developer](https://t.me/e_phador)", disable_web_page_preview= True, quote = True)
	
	
	    
    
bot.run( )