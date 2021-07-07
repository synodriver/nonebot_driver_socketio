# 这是一个示例 Python 脚本。

# 按 Shift+F10 执行或将其替换为您的代码。
# 按 双击 Shift 在所有地方搜索类、文件、工具窗口、操作和设置。
# -*- coding: utf-8 -*-
import nonebot
from nonebot.adapters.cqhttp import Bot

nonebot.init(_env_file=r".env")
driver = nonebot.get_driver()
driver.register_adapter("cqhttp", Bot)

nonebot.load_builtin_plugins()
app = nonebot.get_app()

if __name__ == "__main__":
    try:
        nonebot.run(app="bot:app",ws="wsproto")
    finally:
        print("over")