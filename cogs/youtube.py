import discord
from discord.ext import commands, tasks
import aiohttp
import os
from datetime import datetime

class YouTubeNotifier(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_key = os.getenv('YOUTUBE_API_KEY')
        self.channel_id = "UCBCpr5MvEG6FWTM9ta5cakw" 
        # 채널 ID의 'UC'를 'UU'로 바꾸면 업로드 목록 플레이리스트 ID가 됩니다.
        self.uploads_playlist_id = self.channel_id.replace("UC", "UU", 1)
        self.discord_channel_id = 1422581802479910994
        self.last_video_id = None  
        self.check_youtube.start()

    def cog_unload(self):
        self.check_youtube.cancel()

    @tasks.loop(minutes=5) 
    async def check_youtube(self):
        await self.bot.wait_until_ready()
        
        # [변경점] search 대신 playlistItems 사용 (비용: 100 -> 1)
        # playlistId에 uploads_playlist_id를 넣습니다.
        url = f"https://www.googleapis.com/youtube/v3/playlistItems?part=snippet&playlistId={self.uploads_playlist_id}&maxResults=1&key={self.api_key}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if not data.get("items"):
                            return

                        latest_item = data["items"][0]
                        
                        # [변경점] 데이터 구조가 다릅니다. snippet -> resourceId -> videoId
                        video_id = latest_item["snippet"]["resourceId"]["videoId"]
                        
                        if not video_id:
                            return

                        # 처음 실행 시에는 최신 ID만 저장하고 알림은 보내지 않음
                        if self.last_video_id is None:
                            self.last_video_id = video_id
                            return

                        # 새로운 영상이 발견된 경우
                        if video_id != self.last_video_id:
                            self.last_video_id = video_id
                            
                            title = latest_item["snippet"]["title"]
                            video_url = f"https://www.youtube.com/watch?v={video_id}"
                            
                            channel = self.bot.get_channel(self.discord_channel_id)
                            if channel:
                                embed = discord.Embed(
                                    title="📺 새로운 영상/쇼츠가 업로드되었습니다!",
                                    description=f"**[{title}]({video_url})**",
                                    color=0xFF0000,
                                    timestamp=datetime.now()
                                )
                                # 썸네일 구조는 동일합니다.
                                if "high" in latest_item["snippet"]["thumbnails"]:
                                    embed.set_image(url=latest_item["snippet"]["thumbnails"]["high"]["url"])
                                else:
                                    # high가 없을 경우 default 사용
                                    embed.set_image(url=latest_item["snippet"]["thumbnails"]["default"]["url"])
                                
                                await channel.send(content=f"🔔 **새 영상 알림**", embed=embed)
                    
                    elif response.status == 403:
                        print("유튜브 API 403 에러: 할당량 초과 또는 권한 문제")
                        # API 키가 정확한지, Google Cloud Console에서 YouTube Data API v3가 활성화되어 있는지 확인하세요.
                    else:
                        print(f"유튜브 API 에러: {response.status}")
                        
        except Exception as e:
            print(f"유튜브 체크 중 오류 발생: {e}")

def setup(bot):
    bot.add_cog(YouTubeNotifier(bot))