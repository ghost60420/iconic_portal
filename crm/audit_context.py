from contextvars import ContextVar


_current_actor = ContextVar("crm_audit_actor", default=None)


def set_current_actor(user):
    actor = user if user and getattr(user, "is_authenticated", False) else None
    return _current_actor.set(actor)


def reset_current_actor(token):
    _current_actor.reset(token)


def get_current_actor():
    return _current_actor.get()
