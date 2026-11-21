"""Travel v1 request validation and account APIs."""
import asyncio
from datetime import date as Date, datetime, timedelta
import io
from typing import Literal
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator


from travel_departures import TravelMode, KINDS, directory, search
from travel_profile import Region, Gender
import travel_store
from travel_tools import CHINA, catalog
import web_auth

router = APIRouter(prefix="/api/travel")


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: str | None = Field(None,max_length=100)
    travelers: int | None = Field(None,ge=1,le=99)
    budget_scope: Literal["group","person","per_person"] | None = None
    transport_preference: str | None = Field(None,max_length=100)
    walking_preference: str | None = Field(None,max_length=100)


@router.get("/departures")
def departures(mode: TravelMode, q: str = ""):
    return search(mode,q)


class Ticket(BaseModel):
    date: Date | None = None
    arrival_time: str | None = Field(None,pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    arrival_station: str | None = Field(None,max_length=80)
    train_number: str | None = Field(None,max_length=20)
    latest_entry: str | None = Field(None,pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    reservation_date: Date | None = None


class Trip(Profile):
    travel_mode: TravelMode | None = None
    departure_id: str | None = Field(None,max_length=40)
    travelers: int | None = Field(None,strict=True,ge=1,le=10)
    rooms: int | None = Field(None,ge=1,le=99)
    poi_ids: list[str] = Field(default_factory=list,max_length=20)
    start_date: Date | None = None
    end_date: Date | None = None
    timezone: Literal["Asia/Shanghai"]="Asia/Shanghai"
    budget_fen: int | None = Field(None,ge=0,le=10**10)
    include_round_trip: bool | None = None
    ticket_fields: Ticket | None = None

    @model_validator(mode="after")
    def check_dates(self):
        if self.travel_mode is not None or self.departure_id is not None:
            row = directory()[1].get(self.departure_id)
            if not row or row["kind"] != KINDS.get(self.travel_mode):
                raise ValueError("请从当前出行方式的目录选择出发地")
            self.origin = row["name"]
            if self.budget_scope not in (None, "group"):
                raise ValueError("新行程预算须按全团计算")
            self.budget_scope = "group"
        if self.start_date and self.end_date and not 0 <= (self.end_date-self.start_date).days < 31:
            raise ValueError("行程日期范围无效")
        return self

    def missing(self):
        return [k for k in ("origin","travelers","poi_ids","start_date","end_date","budget_scope","budget_fen","include_round_trip")
                if getattr(self,k) in (None,"",[])]


class Draft(BaseModel):
    task: str = Field(max_length=6000)
    overrides: Trip = Field(default_factory=Trip)
    attachment_ids: list[str] = Field(default_factory=list,max_length=8)


def owner(request):
    store=web_auth.store_for(request)
    if not store or not getattr(request.state,"user",None):
        raise HTTPException(401,"请登录文旅账号")
    return store,request.state.user["id"]


@router.get("/credits")
async def balance(request:Request):
    return await asyncio.to_thread(travel_store.account_data,*owner(request),"credits")


@router.get("/profile")
async def profile(request:Request):
    return await asyncio.to_thread(travel_store.account_data,*owner(request),"profile")


class EditableProfile(Profile):
    age: int | None = Field(None,strict=True,ge=1,le=120)
    gender: Gender | None = None
    intended_region: Region | None = None
    interests: str | None = Field(None,max_length=120)


@router.patch("/profile")
async def patch_profile(body:EditableProfile,request:Request):
    return await asyncio.to_thread(travel_store.account_data,*owner(request),"profile",body.model_dump(exclude_unset=True))


@router.post("/drafts/preview")
async def preview(body:Draft,request:Request):
    defaults={}
    if web_auth.store_for(request):
        defaults={key:value for key,value in (await profile(request)).items() if key in Profile.model_fields}
    fields=body.overrides.model_dump(exclude_unset=True)
    if not fields.get("poi_ids"):
        cards=catalog()
        matches=[k for k,c in cards.items() if c["name"] in body.task]
        fields["poi_ids"]=[k for k in matches if not any(cards[s].get("parent_id")==k for s in matches)]
    if "明天" in body.task:
        fields.setdefault("start_date",datetime.now(CHINA).date()+timedelta(days=1))
    known=catalog()
    named=[k for k,c in known.items() if c.get("parent_id") in fields.get("poi_ids",[]) and c["name"] in body.task]
    if named:
        fields["poi_ids"]=[p for p in fields["poi_ids"] if p not in {known[k]["parent_id"] for k in named}]+named
    if fields.get("start_date") and any(w in body.task for w in ("三天","3天")):
        fields.setdefault("end_date",fields["start_date"]+timedelta(days=2))
    trip=Trip.model_validate({**defaults,**fields})
    return {"trip":trip.model_dump(exclude_none=True),"missing_fields":trip.missing()}


class Order(BaseModel):
    sku:str


class Outcome(BaseModel):
    outcome:Literal["success","failed","cancelled"]


@router.post("/demo-orders")
async def order(body:Order,request:Request):
    return await asyncio.to_thread(travel_store.order,*owner(request),sku=body.sku)


@router.post("/demo-orders/{oid}/complete")
async def complete(oid:str,body:Outcome,request:Request):
    return await asyncio.to_thread(travel_store.order,*owner(request),order_id=oid,outcome=body.outcome)


def store_image(store,uid,data):
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data),formats=("JPEG","PNG","WEBP")) as image:
            if image.width*image.height>12_000_000:
                raise HTTPException(413,"图片尺寸过大")
            image.load()
            out=io.BytesIO()
            image.convert("RGB").save(out,format="JPEG",quality=85)
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError):
        raise HTTPException(422,"图片格式无效")
    aid=uuid.uuid4().hex
    with store.cursor() as c:
        c.execute("INSERT INTO travel_attachments(id,owner_id,content) VALUES (%s,%s,%s)",(aid,uid,out.getvalue()))
    from travel_ticket import recognize
    return {"attachment_id":aid,"suggested_fields":recognize(out.getvalue()),"message":"请确认或手动填写票面信息"}


@router.post("/attachments")
async def upload(request:Request,file:UploadFile=File(...)):
    store,uid=owner(request)
    data=await file.read(5*1024*1024+1)
    if len(data)>5*1024*1024:
        raise HTTPException(413,"图片过大")
    return await asyncio.to_thread(store_image,store,uid,data)


@router.api_route("/attachments/{aid}",methods=["GET","DELETE"])
def attachment(aid:str,request:Request):
    store,uid=owner(request)
    with store.cursor() as c:
        c.execute("SELECT content FROM travel_attachments WHERE id=%s AND owner_id=%s",(aid,uid))
        row=c.fetchone()
        if not row:
            raise HTTPException(404,"附件不存在")
        if request.method=="DELETE":
            c.execute("DELETE FROM travel_attachments WHERE id=%s AND owner_id=%s",(aid,uid))
            return Response(status_code=204)
    return Response(row["content"],media_type="image/jpeg",headers={"Cache-Control":"private, no-store"})


@router.get("/home-weather")
def home_weather():
    return {"location":"贵阳","kind":"unknown","condition":"暂无新鲜降雨观测","as_of":None,"stale":True,"data_mode":"live"}


@router.get("/scenic-cards/{poi_id}")
def scenic_card(poi_id:str):
    card=catalog().get(poi_id)
    if not card:
        raise HTTPException(404,"景点不存在")
    return card
