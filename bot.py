import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler
from telegram.ext.filters import Filters
from pytube import YouTube,Playlist
import time
import os

def start(update, context):
    update.message.reply_text("Hello! Give Me The YouTube Playlist Code")

# Function to handle videos of a youtube playlist
def handle_video(update, context):
    yid = update.message.text
    get_video_from_playlist(update,yid,'720p')
    time.sleep(10)
    #remove the downloaded playlist directory after job done!
    os.system(f"rm -rf {yid}/")

def get_video_from_playlist(update,playlist_id,resolution):
    URL_PLAYLIST = f"https://www.youtube.com/playlist?list={playlist_id}"

    # Retrieve URLs of videos from playlist
    playlist = Playlist(URL_PLAYLIST)
    update.message.reply_text(f'Number Of Videos In playlist: {len(playlist.video_urls)}\nI will send them for you:')

    urls = []
    for url in playlist:
        urls.append(url)
    #download videos from playlist and reply them to user of bot
    c = 1
    for url in urls:
        try: # handle the age restriction error
            yt = YouTube(url)
            video_streams = yt.streams.filter(progressive=True)
        except Exception as e:
            print("stream error: ",e)
            c+=1
            continue
        desired_resolution = resolution
        filtered_streams = video_streams.filter(res=desired_resolution)
        video_stream = filtered_streams.first()
        video_file_name = f"Video-{desired_resolution}-{c:03}.mp4"
        video_stream.download(output_path=f"./{playlist_id}/",filename=video_file_name)
        time.sleep(3)
        try:
            time.sleep(3)
            update.message.reply_chat_action(telegram.constants.CHATACTION_UPLOAD_DOCUMENT)
            with open(f"./{playlist_id}/"+video_file_name, 'rb') as f:
                update.message.reply_document(f)
        except Exception as e:
            print("1st unsuccessfull try:",e)
            try:
                time.sleep(3)
                update.message.reply_chat_action(telegram.constants.CHATACTION_UPLOAD_DOCUMENT)
                with open(f"./{playlist_id}/"+video_file_name, 'rb') as f:
                    update.message.reply_document(f)
            except Exception as e:
                print("2nd unsuccessfull try:",e)
                c+=1
                continue
        c+=1
    update.message.reply_text("Finished")

def main():
    updater = Updater("BOT_TOKEN", use_context=True, 
                      base_url="http://0.0.0.0:8081/bot") # if you run a local telegram bot api

    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text, handle_video))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
