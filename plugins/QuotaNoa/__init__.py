from nonebot import get_driver, require
from nonebot.plugin import PluginMetadata

from .config import Config

require("nonebot_plugin_alconna")

from . import state as state  # noqa: E402
from . import commands as commands  # noqa: E402, F401
from .cpa.client import close_client  # noqa: E402
from .cpa.oauth import cancel_all  # noqa: E402
from .render.html import close_renderer  # noqa: E402
from nonebot_plugin_alconna import __supported_adapters__  # noqa: E402

__plugin_meta__ = PluginMetadata(
    name="QuotaNoa",
    description="多渠额度查询与 CLIProxyAPI 管理：CPA 各平台额度汇总、火山方舟 Coding Plan、WorkBuddy 网关、OAuth 登录与凭证巡检",
    usage="/quota 查额度（/quota 火山 查火山方舟、/quota wb 查 WorkBuddy）；/cpa 管理 CLIProxyAPI",
    type="application",
    config=Config,
    supported_adapters=__supported_adapters__,
    extra={
        "author": "QuotaNoa-Bot",
        "version": "0.3.0",
    },
)

driver = get_driver()


@driver.on_startup
async def _startup() -> None:
    # 惰性加载配置快照，尽早暴露配置错误。
    state.get_snapshot()


@driver.on_shutdown
async def _shutdown() -> None:
    await cancel_all()
    await close_renderer()
    await close_client()
