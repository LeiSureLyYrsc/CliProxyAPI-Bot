from nonebot import get_driver, get_plugin_config, require
from nonebot.plugin import PluginMetadata

from .config import Config

require("nonebot_plugin_alconna")

from . import commands as commands  # noqa: E402, F401
from .client import close_client  # noqa: E402
from .oauth import cancel_all  # noqa: E402
from .render import close_renderer  # noqa: E402
from nonebot_plugin_alconna import __supported_adapters__  # noqa: E402

__plugin_meta__ = PluginMetadata(
    name="CliProxyAPI 管理",
    description="通过聊天管理 CLIProxyAPI：OAuth 登录、凭证巡检、按平台额度汇总、开关账号",
    usage="cpa status / cpa auth list / cpa quota [platform] / cpa login <provider>",
    type="application",
    config=Config,
    supported_adapters=__supported_adapters__,
    extra={
        "author": "CliProxyAPI-Bot",
        "version": "0.1.0",
    },
)

config = get_plugin_config(Config)
driver = get_driver()


@driver.on_shutdown
async def _shutdown() -> None:
    await cancel_all()
    await close_renderer()
    await close_client()
