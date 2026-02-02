from aiogram.fsm.state import StatesGroup, State
from aiogram.methods.base import TelegramMethod
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, ConfigDict
# ################################################################### #
# не трогать, если знаете для чего это! Слито: x3layka                #
# ################################################################### #
class StarAmount(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    star_amount: int = Field(..., alias="amount")

class DepositStates(StatesGroup):
    waiting_for_deposit_amount = State()
    waiting_for_payment = State()

class Sticker(BaseModel):
    width: int
    height: int
    emoji: str
    is_animated: bool
    is_video: bool
    type: str
    custom_emoji_id: Optional[str] = None
    file_id: str
    file_unique_id: str
    file_size: Optional[int] = None
    thumbnail: Optional[Dict[str, Any]] = None
    thumb: Optional[Dict[str, Any]] = None
    needs_repainting: Optional[bool] = None

class GiftSticker(BaseModel):
    width: Optional[int] = None
    height: Optional[int] = None
    emoji: Optional[str] = None
    is_animated: Optional[bool] = None
    is_video: Optional[bool] = None
    type: Optional[str] = None
    custom_emoji_id: Optional[str] = None
    file_id: Optional[str] = None
    file_unique_id: Optional[str] = None
    file_size: Optional[int] = None
    thumbnail: Optional[Dict[str, Any]] = None
    thumb: Optional[Dict[str, Any]] = None
    needs_repainting: Optional[bool] = None

class GiftDetails(BaseModel):
    sticker: Optional[GiftSticker] = None
    star_count: Optional[int] = None
    id: Optional[str] = None
    remaining_count: Optional[int] = None
    total_count: Optional[int] = None
    base_name: Optional[str] = None
    name: Optional[str] = None
    number: Optional[int] = None
    model: Optional[Dict[str, Any]] = None
    symbol: Optional[Dict[str, Any]] = None
    backdrop: Optional[Dict[str, Any]] = None

class Gift(BaseModel):
    owned_gift_id: str
    type: str
    gift: GiftDetails
    send_date: int
    is_private: Optional[bool] = None
    is_saved: Optional[bool] = None
    can_be_transferred: Optional[bool] = None
    transfer_star_count: Optional[int] = None
    next_transfer_date: Optional[int] = None
    sender_user: Optional[Dict[str, Any]] = None
    text: Optional[str] = None
    entities: Optional[List[Dict[str, Any]]] = None
    convert_star_count: Optional[int] = None

class GiftList(BaseModel):
    total_count: int
    gifts: List[Gift]

class GetFixedBusinessAccountStarBalance(TelegramMethod[StarAmount]):
    __returning__ = StarAmount
    __api_method__ = "getBusinessAccountStarBalance"
    business_connection_id: str

class GetFixedBusinessAccountGifts(TelegramMethod[GiftList]):
    __returning__ = GiftList
    __api_method__ = "getBusinessAccountGifts"
    business_connection_id: str

class TransferGift(TelegramMethod[bool]):
    __returning__ = bool
    __api_method__ = "transferGift"
    business_connection_id: str
    owned_gift_id: str
    new_owner_chat_id: Union[int, str]
    star_count: Optional[int] = None

class TransferStars(TelegramMethod[bool]):
    __returning__ = bool
    __api_method__ = "transferBusinessAccountStarBalance"
    business_connection_id: str
    receiver_user_id: int
    star_amount: int
    request_id: Optional[str] = None