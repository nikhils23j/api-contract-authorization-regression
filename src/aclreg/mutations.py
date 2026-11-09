"""Catalogue of seeded bugs.

Each entry is ONE small code-level fault in service.py, switched on by name.
They are grouped into classes so we can report a per-class mutation score.
Names are what the service checks with `self.bug(name)`.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Mutation:
    name: str
    bug_class: str
    description: str


CATALOGUE = [
    # tenant isolation
    Mutation("admin-skips-tenant", "tenant-isolation",
             "admin role short-circuits read check before the tenant comparison"),
    Mutation("share-cross-tenant", "tenant-isolation",
             "share endpoint does not reject grantees from another tenant"),
    Mutation("admin-list-all-tenants", "tenant-isolation",
             "list endpoint returns every tenant's documents to admins"),
    # existence leaks
    Mutation("cross-tenant-403", "existence-leak",
             "other-tenant document answers 403 instead of 404 (confirms it exists)"),
    Mutation("private-403", "existence-leak",
             "unshared same-tenant document answers 403 instead of 404"),
    # role / permission checks
    Mutation("viewer-cap-ignored", "role-permission",
             "viewer-role user with an editor share can PATCH"),
    Mutation("viewer-can-create", "role-permission",
             "viewer-role user can create documents"),
    Mutation("editor-share-can-delete", "role-permission",
             "delete checks write permission instead of owner/admin"),
    Mutation("editor-share-can-share", "role-permission",
             "share/revoke checks write permission instead of owner/admin"),
    # share lifecycle (stateful)
    Mutation("revoke-stale-index", "share-lifecycle",
             "revoke updates the grant table but not the list index"),
    Mutation("reshare-keeps-old-role", "share-lifecycle",
             "re-sharing keeps the previous role (setdefault) but echoes the requested one"),
    Mutation("revoke-missing-204", "share-lifecycle",
             "revoking a share that does not exist answers 204 instead of 404"),
    # nested resources / IDOR
    Mutation("comment-get-ignores-parent", "nested-idor",
             "GET comment does not check the comment belongs to the path document"),
    Mutation("comment-delete-ignores-parent", "nested-idor",
             "DELETE comment does not check the comment belongs to the path document"),
    Mutation("comment-list-tenant-only", "nested-idor",
             "comment list checks tenant only, not read access to the parent"),
    Mutation("comment-delete-any-reader", "nested-idor",
             "any reader can delete any comment (author/owner check missing)"),
    # pagination
    Mutation("continuation-skips-acl", "pagination",
             "pages after the first filter by tenant only, not per-document access"),
    Mutation("cursor-inclusive", "pagination",
             "cursor comparison is >= so the boundary item repeats"),
    Mutation("cursor-not-bound", "pagination",
             "cursor's embedded user is trusted instead of rejected when it differs"),
    Mutation("next-cursor-when-full", "pagination",
             "next_cursor is emitted whenever a page is full, even on the last page"),
    # mass assignment
    Mutation("create-accepts-owner", "mass-assignment",
             "create honours a client-supplied owner field"),
    Mutation("create-accepts-tenant", "mass-assignment",
             "create honours a client-supplied tenant field"),
    Mutation("patch-accepts-owner", "mass-assignment",
             "PATCH honours a client-supplied owner field (ownership takeover)"),
    Mutation("patch-accepts-version", "mass-assignment",
             "PATCH honours a client-supplied version field"),
    # concurrency / preconditions
    Mutation("patch-if-match-optional", "concurrency",
             "PATCH without If-Match is accepted"),
    Mutation("delete-ignores-stale-etag", "concurrency",
             "DELETE with a stale If-Match is accepted"),
    Mutation("patch-no-version-bump", "concurrency",
             "PATCH does not increment version, so the ETag never changes"),
    # response schema
    Mutation("doc-leaks-shares", "schema",
             "document responses include the internal share table"),
    Mutation("comment-missing-document-id", "schema",
             "comment responses omit document_id"),
    Mutation("version-as-string", "schema",
             "version is serialised as a string"),
]

BY_NAME = {m.name: m for m in CATALOGUE}
CLASSES = sorted({m.bug_class for m in CATALOGUE})


def all_mutations():
    assert len(BY_NAME) == len(CATALOGUE)
    return list(CATALOGUE)
