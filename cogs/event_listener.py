import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
import json
import os

# 1. 한국 시간(KST) 정의
KST = timezone(timedelta(hours=9))

class EventListener(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.target_channel_id = 1472943305644965888
        self.allowed_roles = ["!"]
        self.db_file = "schedules.json"
        self.meeting_schedule = self.load_schedules()
        self.check_schedule.start()

    def cog_unload(self):
        self.check_schedule.cancel()

    def load_schedules(self):
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        dt = datetime.fromisoformat(item["time"])
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=KST)
                        item["time"] = dt
                    return data
            except Exception as e:
                print(f"데이터 로드 에러: {e}")
                return []
        return []

    def save_schedules(self):
        try:
            data_to_save = []
            for item in self.meeting_schedule:
                copy_item = item.copy()
                copy_item["time"] = item["time"].isoformat()
                data_to_save.append(copy_item)
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"데이터 저장 에러: {e}")

    async def is_admin(self, ctx):
        if not hasattr(ctx.author, "roles"): return False
        user_role_names = [role.name for role in ctx.author.roles]
        return any(role in user_role_names for role in self.allowed_roles)

    async def get_target_channel(self):
        channel = self.bot.get_channel(self.target_channel_id)
        if not channel:
            try: channel = await self.bot.fetch_channel(self.target_channel_id)
            except: return None
        return channel

    @discord.slash_command(name="일정", description="새 일정을 예약합니다. (관리진 전용)")
    async def set_meeting(
        self, ctx, 
        일시: str = discord.Option(description="MM/DD HH:MM (예: 02/17 14:30)"), 
        내용: str = discord.Option(description="일정 내용"),
        # Option의 첫 번째 인자에 타입을 명시해야 AttributeError를 방지할 수 있습니다.
        대상1: discord.abc.Mentionable = discord.Option(discord.abc.Mentionable, default=None, description="대상 1"),
        대상2: discord.abc.Mentionable = discord.Option(discord.abc.Mentionable, default=None, description="대상 2"),
        대상3: discord.abc.Mentionable = discord.Option(discord.abc.Mentionable, default=None, description="대상 3"),
        대상4: discord.abc.Mentionable = discord.Option(discord.abc.Mentionable, default=None, description="대상 4"),
        대상5: discord.abc.Mentionable = discord.Option(discord.abc.Mentionable, default=None, description="대상 5"),
        대상6: discord.abc.Mentionable = discord.Option(discord.abc.Mentionable, default=None, description="대상 6"),
        대상7: discord.abc.Mentionable = discord.Option(discord.abc.Mentionable, default=None, description="대상 7"),
        대상8: discord.abc.Mentionable = discord.Option(discord.abc.Mentionable, default=None, description="대상 8"),
        대상9: discord.abc.Mentionable = discord.Option(discord.abc.Mentionable, default=None, description="대상 9"),
        대상10: discord.abc.Mentionable = discord.Option(discord.abc.Mentionable, default=None, description="대상 10")
    ):
        if not await self.is_admin(ctx):
            return await ctx.respond("❌ 권한이 없습니다.", ephemeral=True)

        try:
            now = datetime.now(KST)
            parsed_time = datetime.strptime(일시, "%m/%d %H:%M")
            target_time = parsed_time.replace(year=now.year, tzinfo=KST)

            if target_time < now - timedelta(days=1):
                target_time = target_time.replace(year=now.year + 1)

            targets = [대상1, 대상2, 대상3, 대상4, 대상5, 대상6, 대상7, 대상8, 대상9, 대상10]
            
            # 버그 수정 포인트: t.mention이 가능한지 확인하고, 불가능하면 ID를 이용해 강제로 멘션 형태를 만듭니다.
            mentions_list = []
            for t in targets:
                if t:
                    if hasattr(t, "mention"):
                        mentions_list.append(t.mention)
                    else:
                        # 만약 문자열(ID)로 들어왔을 경우 강제로 멘션 포맷 생성
                        mentions_list.append(f"<@{t}>" if str(t).isdigit() else str(t))
            
            mentions = " ".join(mentions_list)

            meeting_info = {
                "id": ctx.interaction.id,
                "author": ctx.author.display_name,
                "time": target_time,
                "content": 내용,
                "mention": mentions,
                "notified_10m": False
            }
            
            self.meeting_schedule.append(meeting_info)
            self.meeting_schedule.sort(key=lambda x: x["time"])
            self.save_schedules()

            await ctx.respond(f"✅ 일정 등록 완료: **{내용}**")

            channel = await self.get_target_channel()
            if channel:
                embed = discord.Embed(title="📅 새 일정 알림", color=0x5865F2)
                embed.add_field(name="시간", value=target_time.strftime('%Y-%m-%d %H:%M'), inline=True)
                embed.add_field(name="내용", value=내용, inline=True)
                await channel.send(content=f"{mentions} 확인해주세요!" if mentions else None, embed=embed)

        except ValueError:
            await ctx.respond("❌ 형식이 잘못되었습니다. (MM/DD HH:MM)", ephemeral=True)

    # ... (나머지 list_meetings, cancel_meeting, check_schedule 함수는 동일하므로 중략)
    # 아래는 동일한 코드를 유지하시면 됩니다.

    @discord.slash_command(name="일정목록", description="예약된 일정을 확인합니다.")
    async def list_meetings(self, ctx: discord.ApplicationContext):
        if not self.meeting_schedule:
            return await ctx.respond("📅 예약된 일정이 없습니다.")

        embed = discord.Embed(title="📋 일정 목록 (KST)", color=0x3498db)
        for i, m in enumerate(self.meeting_schedule[:10], 1):
            time_str = m["time"].strftime('%m/%d %H:%M')
            embed.add_field(
                name=f"{i}. {m['content']}",
                value=f"⏰ `{time_str}` | 👤 {m['mention'] or '없음'}",
                inline=False
            )
        await ctx.respond(embed=embed)

    @discord.slash_command(name="일정취소", description="일정을 취소합니다.")
    async def cancel_meeting(self, ctx, 번호: int):
        if not await self.is_admin(ctx):
            return await ctx.respond("❌ 권한이 없습니다.", ephemeral=True)

        if 0 < 번호 <= len(self.meeting_schedule):
            removed = self.meeting_schedule.pop(번호 - 1)
            self.save_schedules()
            await ctx.respond(f"🗑️ 취소됨: **{removed['content']}**")
        else:
            await ctx.respond("❌ 번호가 올바르지 않습니다.", ephemeral=True)

    @tasks.loop(seconds=20)
    async def check_schedule(self):
        now = datetime.now(KST)
        channel = await self.get_target_channel()
        if not channel: return

        changed = False
        new_schedule = []

        for m in self.meeting_schedule:
            time_diff = m["time"] - now
            
            if timedelta(minutes=0) < time_diff <= timedelta(minutes=10) and not m.get("notified_10m"):
                embed = discord.Embed(title="⚠️ 일정 10분 전!", description=f"**{m['content']}**", color=0xFFA500)
                await channel.send(content=m["mention"], embed=embed)
                m["notified_10m"] = True
                changed = True

            if now >= m["time"]:
                embed = discord.Embed(title="🚀 일정 시작!", description=f"**{m['content']}**", color=0xFF0000)
                await channel.send(content=m["mention"], embed=embed)
                changed = True
                continue
            
            new_schedule.append(m)

        if changed:
            self.meeting_schedule = new_schedule
            self.save_schedules()

    @check_schedule.before_loop
    async def before_check_schedule(self):
        await self.bot.wait_until_ready()

def setup(bot):
    bot.add_cog(EventListener(bot))