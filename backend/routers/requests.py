from fastapi import APIRouter, Depends
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from auth import get_current_user, get_current_user_optional, require_admin
from database import get_db
from errors import AppError, ErrorCode
from helpers import next_id, utcnow_iso
from models.activity import ActivityEntry
from models.comment import Comment
from models.request import Request
from models.response import OfficialResponse
from models.user import User
from models.vote import Vote
from schemas import (
    MergeIn,
    RequestCreate,
    RequestDelete,
    RequestDetail,
    RequestList,
    RequestUpdate,
    StatusSet,
)
from schemas.common import SORT_NEWEST, STATUS_LABELS, STATUS_REDIRECTED
from serializers import support_count, to_detail, to_summary

router = APIRouter(tags=["requests"])

DESCRIPTION_MIN_LENGTH = 10


@router.get("/requests", response_model=RequestList)
def list_requests(
    q: str = "",
    status: str = "",
    sort: str = "",
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> RequestList:
    stmt = select(Request)
    if status:
        stmt = stmt.where(Request.status == status)
    query = q.strip().lower()
    if query:
        pattern = f"%{query}%"
        stmt = stmt.where(
            func.lower(Request.title + " " + Request.description).like(pattern)
        )
    items = db.scalars(stmt).all()

    supports = {r.id: support_count(db, r.id) for r in items}
    if sort == SORT_NEWEST:
        items.sort(key=lambda r: r.created_at, reverse=True)
    else:
        items.sort(key=lambda r: (supports[r.id], r.created_at), reverse=True)

    return RequestList(
        items=[to_summary(db, r, user.id if user else None) for r in items],
        total=len(items),
    )


@router.get("/requests/{request_id}", response_model=RequestDetail)
def get_request(
    request_id: str,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> RequestDetail:
    request = db.get(Request, request_id)
    if not request:
        raise AppError("Request not found", ErrorCode.NOT_FOUND)
    return to_detail(db, request, user.id if user else None)


@router.post("/requests", response_model=RequestDetail)
def create_request(
    body: RequestCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequestDetail:
    if not body.title or not body.title.strip():
        raise AppError("Title is required")
    description = (body.description or "").strip()
    if len(description) < DESCRIPTION_MIN_LENGTH:
        raise AppError(
            f"Description must be at least {DESCRIPTION_MIN_LENGTH} characters"
        )
    now = utcnow_iso()
    request_id = next_id(db, "request", "req-")
    request = Request(
        id=request_id,
        title=body.title.strip(),
        description=description,
        author_id=user.id,
        status="under_review",
        merged_into=None,
        created_at=now,
        updated_at=now,
    )
    db.add(request)
    db.add(
        ActivityEntry(
            id=next_id(db, "activity", "a-"),
            request_id=request_id,
            type="created",
            actor_id=user.id,
            detail="created this request",
            created_at=now,
        )
    )
    db.commit()
    db.refresh(request)
    return to_detail(db, request, user.id)


@router.patch("/requests/{request_id}", response_model=RequestDetail)
def update_request(
    request_id: str,
    body: RequestUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequestDetail:
    request = db.get(Request, request_id)
    if not request:
        raise AppError("Request not found", ErrorCode.NOT_FOUND)
    if request.author_id != user.id and user.role != "admin":
        raise AppError(
            "Only the author or an admin can edit this request", ErrorCode.FORBIDDEN
        )
    if not body.title or not body.title.strip():
        raise AppError("Title is required")
    description = (body.description or "").strip()
    if len(description) < DESCRIPTION_MIN_LENGTH:
        raise AppError(
            f"Description must be at least {DESCRIPTION_MIN_LENGTH} characters"
        )
    request.title = body.title.strip()
    request.description = description
    request.updated_at = utcnow_iso()
    db.commit()
    db.refresh(request)
    return to_detail(db, request, user.id)


@router.delete("/requests/{request_id}", response_model=RequestDelete)
def delete_request(
    request_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RequestDelete:
    request = db.get(Request, request_id)
    if not request:
        raise AppError("Request not found", ErrorCode.NOT_FOUND)
    if request.author_id != user.id and user.role != "admin":
        raise AppError(
            "Only the author or an admin can delete this request", ErrorCode.FORBIDDEN
        )
    db.execute(delete(Vote).where(Vote.request_id == request_id))
    db.execute(delete(Comment).where(Comment.request_id == request_id))
    db.execute(delete(ActivityEntry).where(ActivityEntry.request_id == request_id))
    db.execute(delete(OfficialResponse).where(OfficialResponse.request_id == request_id))
    db.delete(request)
    db.commit()
    return RequestDelete(id=request_id)


@router.put("/requests/{request_id}/status", response_model=RequestDetail)
def set_status(
    request_id: str,
    body: StatusSet,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> RequestDetail:
    request = db.get(Request, request_id)
    if not request:
        raise AppError("Request not found", ErrorCode.NOT_FOUND)
    if request.status == STATUS_REDIRECTED:
        raise AppError(
            "Redirected requests are locked by a merge", ErrorCode.REDIRECTED_LOCKED
        )
    if body.status == STATUS_REDIRECTED:
        raise AppError(
            "Redirected status is only set by a merge", ErrorCode.REDIRECTED_LOCKED
        )
    if body.status not in STATUS_LABELS:
        raise AppError("Unknown status")
    now = utcnow_iso()
    request.status = body.status
    request.updated_at = now
    db.add(
        ActivityEntry(
            id=next_id(db, "activity", "a-"),
            request_id=request_id,
            type="status_changed",
            actor_id=admin.id,
            detail=f"moved to {STATUS_LABELS[body.status]}",
            created_at=now,
        )
    )
    db.commit()
    db.refresh(request)
    return to_detail(db, request, admin.id)


@router.post("/requests/{request_id}/merge", response_model=RequestDetail)
def merge_requests(
    request_id: str,
    body: MergeIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> RequestDetail:
    if request_id == body.into:
        raise AppError("Cannot merge a request into itself")
    absorbed = db.get(Request, request_id)
    survivor = db.get(Request, body.into)
    if not absorbed:
        raise AppError("Request not found", ErrorCode.NOT_FOUND)
    if not survivor:
        raise AppError("Merge target not found", ErrorCode.NOT_FOUND)
    if absorbed.status == STATUS_REDIRECTED:
        raise AppError(
            "Request is already redirected by a merge", ErrorCode.REDIRECTED_LOCKED
        )
    if survivor.status == STATUS_REDIRECTED:
        raise AppError("Cannot merge into a Redirected request")

    # Union of voters, deduplicated by user.
    survivor_voters = set(
        db.scalars(select(Vote.user_id).where(Vote.request_id == survivor.id)).all()
    )
    absorbed_votes = db.scalars(
        select(Vote).where(Vote.request_id == absorbed.id)
    ).all()
    for vote in absorbed_votes:
        if vote.user_id in survivor_voters:
            db.delete(vote)
        else:
            vote.request_id = survivor.id
            survivor_voters.add(vote.user_id)

    now = utcnow_iso()
    absorbed.status = STATUS_REDIRECTED
    absorbed.merged_into = survivor.id
    absorbed.updated_at = now
    survivor.updated_at = now

    db.add(
        ActivityEntry(
            id=next_id(db, "activity", "a-"),
            request_id=absorbed.id,
            type="merged_into",
            actor_id=admin.id,
            detail=f"merged into {survivor.id}",
            created_at=now,
        )
    )
    db.add(
        ActivityEntry(
            id=next_id(db, "activity", "a-"),
            request_id=survivor.id,
            type="merged_into",
            actor_id=admin.id,
            detail=f"absorbed {absorbed.id}",
            created_at=now,
        )
    )

    db.commit()
    db.refresh(survivor)
    return to_detail(db, survivor, admin.id)