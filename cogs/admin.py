import discord
from discord.ext import commands
import os
import logging

logger = logging.getLogger(__name__)

class Admin(commands.Cog):
    """
    Admin commands for bot management.
    Provides cog reload functionality with authorization.
    """
    
    def __init__(self, bot: discord.Bot):
        """
        Initialize Admin cog with bot instance and load configuration.
        """
        self.bot = bot
        self.admin_ids = set()
        self.admin_role = None
        self._parse_admin_config()
        logger.info(f"Admin cog initialized with {len(self.admin_ids)} authorized IDs")

    def _parse_admin_config(self) -> None:
        """Parse admin authorization from environment variables."""
        admin_ids_str = os.getenv("ADMIN_IDS", "")
        if admin_ids_str:
            try:
                self.admin_ids = {
                    int(id_str.strip()) 
                    for id_str in admin_ids_str.split(",") 
                    if id_str.strip()
                }
            except ValueError as e:
                print(f"⚠️ ADMIN_IDS 파싱 오류: {e}")
                logger.warning(f"ADMIN_IDS parsing error: {e}")
                self.admin_ids = set()
        else:
            print("⚠️ ADMIN_IDS 환경 변수가 설정되지 않았습니다.")
            logger.warning("ADMIN_IDS environment variable is not set.")
        
        self.admin_role = os.getenv("ADMIN_ROLE", None)
        if not self.admin_role:
            print("⚠️ ADMIN_ROLE 환경 변수가 설정되지 않았습니다.")
            logger.warning("ADMIN_ROLE environment variable is not set.")

    async def is_admin(self, ctx: discord.ApplicationContext | commands.Context) -> bool:
        """Check if the command invoker is authorized as an admin."""
        author_id = ctx.author.id
        
        if author_id in self.admin_ids:
            return True
        
        if self.admin_role and hasattr(ctx.author, "roles"):
            user_role_names = [role.name for role in ctx.author.roles]
            if self.admin_role in user_role_names:
                return True
        
        logger.warning(f"Unauthorized reload attempt by {author_id}")
        return False

    @staticmethod
    def _build_extension_path(cog_name: str) -> str:
        """Build the full extension path from cog name."""
        return f"cogs.{cog_name}"

    async def _reload_cog(self, ctx: discord.ApplicationContext | commands.Context, cog_name: str) -> None:
        """Core reload logic shared by both command interfaces."""
        if not await self.is_admin(ctx):
            if isinstance(ctx, discord.ApplicationContext):
                await ctx.respond("❌ 권한이 없습니다.", ephemeral=True)
            else:
                await ctx.send("❌ 권한이 없습니다.")
            return
            
        extension_path = self._build_extension_path(cog_name)
        
        if extension_path not in self.bot.extensions:
            available = [ext.split(".")[-1] for ext in self.bot.extensions.keys() if ext.startswith("cogs.")]
            msg = f"❌ 존재하지 않는 Cog입니다: {cog_name}\n사용 가능한 Cogs: {', '.join(available)}"
            if isinstance(ctx, discord.ApplicationContext):
                await ctx.respond(msg)
            else:
                await ctx.send(msg)
            return

        logger.info(f"Reloading cog: {cog_name} by {ctx.author.id}")
        
        try:
            self.bot.reload_extension(extension_path)
            msg = f"✅ {cog_name} 리로드 성공!"
            if isinstance(ctx, discord.ApplicationContext):
                await ctx.respond(msg)
            else:
                await ctx.send(msg)
        except discord.ExtensionFailed as e:
            logger.error(f"Failed to reload {cog_name}: {e}")
            msg = f"❌ 리로드 중 오류 발생:\n```python\n{e}\n```"
            if isinstance(ctx, discord.ApplicationContext):
                await ctx.respond(msg)
            else:
                await ctx.send(msg)
        except discord.ExtensionNotLoaded as e:
            logger.error(f"Extension not loaded {cog_name}: {e}")
            msg = f"❌ {cog_name}이(가) 로드되지 않았습니다."
            if isinstance(ctx, discord.ApplicationContext):
                await ctx.respond(msg)
            else:
                await ctx.send(msg)
        except SyntaxError as e:
            logger.error(f"Syntax error reloading {cog_name}: {e}")
            msg = f"❌ 문법 오류:\n```python\n{e}\n```"
            if isinstance(ctx, discord.ApplicationContext):
                await ctx.respond(msg)
            else:
                await ctx.send(msg)
        except ModuleNotFoundError as e:
            logger.error(f"Module not found {cog_name}: {e}")
            msg = f"❌ Cog 파일을 찾을 수 없습니다: {cog_name}"
            if isinstance(ctx, discord.ApplicationContext):
                await ctx.respond(msg)
            else:
                await ctx.send(msg)
        except Exception as e:
            logger.error(f"Unexpected error reloading {cog_name}: {e}")
            msg = f"❌ 예기치 않은 오류:\n```python\n{type(e).__name__}: {e}\n```"
            if isinstance(ctx, discord.ApplicationContext):
                await ctx.respond(msg)
            else:
                await ctx.send(msg)

    @discord.slash_command(name="reload", description="Cog를 다시 로드합니다 (관리자 전용)")
    async def reload_slash(
        self, 
        ctx: discord.ApplicationContext,
        cog_name: str = discord.Option(description="리로드할 Cog 이름 (예: general)")
    ):
        """Slash command interface for cog reload."""
        await self._reload_cog(ctx, cog_name)
        
    @commands.command(name="reload")
    async def reload_message(self, ctx: commands.Context, cog_name: str):
        """Message command interface for cog reload."""
        await self._reload_cog(ctx, cog_name)

def setup(bot: discord.Bot):
    bot.add_cog(Admin(bot))
