import os
from dataclasses import dataclass

@dataclass
class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN")
    # ID канала, куда будем постить (можно убрать, если будешь передавать его через команду)
    TARGET_CHANNEL_ID: int = int(os.getenv("TARGET_CHANNEL_ID", "-1001234567890")) 

config = Config()
