"""
ConnectAI — Notification helpers
All notification creation logic lives here.
"""
from database import create_notification


def notify_seeker_matched(seeker_id, match_id, helper_first):
    create_notification(
        user_id=seeker_id,
        notif_type="match_found",
        message=f"🎯 We found your buddy! {helper_first} has been notified and will respond soon.",
        match_id=match_id,
    )


def notify_helper_requested(helper_id, match_id, seeker_first):
    create_notification(
        user_id=helper_id,
        notif_type="help_requested",
        message=f"👋 {seeker_first} needs your help! Review their request.",
        match_id=match_id,
    )


def notify_seeker_accepted(seeker_id, match_id, helper_first):
    create_notification(
        user_id=seeker_id,
        notif_type="match_accepted",
        message=f"✅ {helper_first} accepted your match! Go say hello.",
        match_id=match_id,
    )


def notify_seeker_declined(seeker_id, match_id):
    create_notification(
        user_id=seeker_id,
        notif_type="match_declined",
        message="🔄 Your previous match declined. Submit again to find a new buddy!",
        match_id=match_id,
    )


def notify_match_expired(seeker_id, match_id):
    create_notification(
        user_id=seeker_id,
        notif_type="match_expired",
        message="⏰ Your match request expired (no response in 48 hrs). Submit a new request!",
        match_id=match_id,
    )
