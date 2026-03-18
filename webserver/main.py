from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Optional
import hashlib
import os
import secrets

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import httpx
from sqlalchemy import func
from sqlmodel import Field, Session, SQLModel, create_engine, select

load_dotenv()


class TokenRecord(SQLModel, table=True):
    uid: int = Field(primary_key=True)
    token_hash: str = Field(index=True, unique=True)
    is_moderator: bool = False


class RelayLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    uid: Optional[int] = Field(default=None, index=True)
    created_at: int = Field(
        default_factory=lambda: int(datetime.now(timezone.utc).timestamp()),
        index=True,
    )
    status_code: int


sqlite_file_name = "database.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"
connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, connect_args=connect_args)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_root_uid() -> int:
    root_uid_raw = os.getenv("ROOT_ID")
    if root_uid_raw is None or not root_uid_raw.strip():
        raise RuntimeError("ROOT_ID is required")
    return int(root_uid_raw)


def bootstrap_root_moderator():
    root_uid = get_root_uid()

    with Session(engine) as session:
        existing_mod = session.exec(
            select(TokenRecord).where(TokenRecord.is_moderator == True)
        ).first()

        if existing_mod:
            return

        root_token = os.getenv("ROOT_TOKEN")
        generated = False

        if not root_token:
            root_token = secrets.token_urlsafe(48)
            generated = True

        root_record = TokenRecord(
            uid=root_uid,
            token_hash=hash_token(root_token),
            is_moderator=True,
        )
        session.add(root_record)
        session.commit()

        print("\n[BOOTSTRAP] No moderator found. Created initial root moderator.")
        print(f"[BOOTSTRAP] id={root_uid}")
        if generated:
            print(f"[BOOTSTRAP] Generated ROOT TOKEN: {root_token}")
            print("[BOOTSTRAP] Save this now. It will not be shown again.\n")
        else:
            print("[BOOTSTRAP] Used ROOT_TOKEN from environment.\n")


def get_session():
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_db_and_tables()
    bootstrap_root_moderator()
    yield


app = FastAPI(lifespan=lifespan)

bearer_scheme = HTTPBearer(auto_error=False)


def get_bearer_token(
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)
    ],
) -> str:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    return credentials.credentials


def get_current_member(
    token: Annotated[str, Depends(get_bearer_token)],
    session: SessionDep,
) -> TokenRecord:
    token_hash = hash_token(token)

    row = session.exec(
        select(TokenRecord).where(TokenRecord.token_hash == token_hash)
    ).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return row


def get_current_moderator(
    member: Annotated[TokenRecord, Depends(get_current_member)],
) -> TokenRecord:
    if not member.is_moderator:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Moderator access required",
        )
    return member


MemberDep = Annotated[TokenRecord, Depends(get_current_member)]
ModeratorDep = Annotated[TokenRecord, Depends(get_current_moderator)]


def create_relay_log(
    session: Session,
    uid: int,
    status_code: int,
) -> RelayLog:
    row = RelayLog(uid=uid, status_code=status_code)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@app.get("/tokens")
def list_tokens(
    _moderator: ModeratorDep,
    session: SessionDep,
):
    rows = session.exec(select(TokenRecord)).all()
    return [
        {
            "uid": row.uid,
            "is_moderator": row.is_moderator,
        }
        for row in rows
    ]


@app.post("/tokens/{uid}")
def set_token(
    uid: int,
    _moderator: ModeratorDep,
    session: SessionDep,
):
    if uid == get_root_uid():
        raise HTTPException(
            status_code=403,
            detail="Cannot modify root token",
        )

    row = session.get(TokenRecord, uid)

    new_token = secrets.token_urlsafe(48)
    hashed_new_token = hash_token(new_token)

    if row:
        row.token_hash = hashed_new_token
    else:
        row = TokenRecord(
            uid=uid,
            token_hash=hashed_new_token,
            is_moderator=False,
        )
        session.add(row)

    session.commit()
    session.refresh(row)

    return {
        "uid": row.uid,
        "is_moderator": row.is_moderator,
        "token": new_token,
    }


@app.delete("/tokens/{uid}")
def delete_token(
    uid: int,
    _moderator: ModeratorDep,
    session: SessionDep,
):
    if uid == get_root_uid():
        raise HTTPException(
            status_code=403,
            detail="Cannot delete root token",
        )

    row = session.get(TokenRecord, uid)
    if not row:
        raise HTTPException(status_code=404, detail="Token not found")

    session.delete(row)
    session.commit()
    return {"ok": True}


@app.get("/moderators/{uid}")
def get_moderator(
    uid: int,
    _moderator: ModeratorDep,
    session: SessionDep,
):
    row = session.get(TokenRecord, uid)
    if not row:
        raise HTTPException(status_code=404, detail="User token not found")

    return {
        "uid": row.uid,
        "is_moderator": row.is_moderator,
    }


@app.post("/moderators/{uid}")
def promote_to_moderator(
    uid: int,
    _moderator: ModeratorDep,
    session: SessionDep,
):
    if uid == get_root_uid():
        raise HTTPException(
            status_code=403,
            detail="Root user is already a moderator",
        )

    row = session.get(TokenRecord, uid)
    if not row:
        raise HTTPException(
            status_code=404,
            detail="User token not found",
        )

    if row.is_moderator:
        raise HTTPException(
            status_code=400,
            detail="User is already a moderator",
        )

    row.is_moderator = True
    session.commit()
    session.refresh(row)

    return {
        "uid": row.uid,
        "is_moderator": row.is_moderator,
    }


@app.delete("/moderators/{uid}")
def demote_moderator(
    uid: int,
    moderator: ModeratorDep,
    session: SessionDep,
):

    if uid == moderator.uid:
        raise HTTPException(
            status_code=400,
            detail="You cannot demote yourself",
        )

    if uid == get_root_uid():
        raise HTTPException(
            status_code=403,
            detail="Cannot demote root user",
        )

    row = session.get(TokenRecord, uid)
    if not row:
        raise HTTPException(
            status_code=404,
            detail="User token not found",
        )

    if not row.is_moderator:
        raise HTTPException(
            status_code=400,
            detail="User is not a moderator",
        )

    row.is_moderator = False
    session.commit()
    session.refresh(row)

    return {
        "uid": row.uid,
        "is_moderator": row.is_moderator,
    }


@app.get("/relay/logs")
def list_relay_logs(
    _moderator: ModeratorDep,
    session: SessionDep,
    page: int = 1,
    page_size: int = 10,
):
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be at least 1")

    if page_size < 1 or page_size > 25:
        raise HTTPException(
            status_code=400,
            detail="page_size must be between 1 and 25",
        )

    total = session.exec(select(func.count()).select_from(RelayLog)).one()
    offset = (page - 1) * page_size

    rows = session.exec(
        select(RelayLog)
        .order_by(RelayLog.created_at.desc(), RelayLog.id.desc())
        .offset(offset)
        .limit(page_size)
    ).all()

    total_pages = (total + page_size - 1) // page_size if total else 1

    return {
        "items": [
            {
                "uid": row.uid,
                "created_at": row.created_at,
                "status_code": row.status_code,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


@app.post("/relay")
async def relay_webhook(
    request: Request,
    member: MemberDep,
    session: SessionDep,
):
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        create_relay_log(session, member.uid, 500)
        raise HTTPException(
            status_code=500,
            detail="WEBHOOK_URL is not configured",
        )

    body = await request.body()

    headers = {}
    content_type = request.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            discord_response = await client.post(
                webhook_url,
                content=body,
                headers=headers,
            )
    except httpx.HTTPError:
        create_relay_log(session, member.uid, 502)
        raise HTTPException(
            status_code=502,
            detail="Failed to reach Discord webhook",
        )

    create_relay_log(session, member.uid, discord_response.status_code)

    return Response(
        content=discord_response.content,
        status_code=discord_response.status_code,
        media_type=discord_response.headers.get("content-type", "application/json"),
    )
