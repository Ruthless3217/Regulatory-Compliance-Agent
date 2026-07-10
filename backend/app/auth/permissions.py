ROLE_PERMISSIONS = {
    "user": {
        "submission:create", "submission:read", "submission:delete", "analysis:run",
        "chat:use", "comparison:use", "dashboard:view", "knowledgebase:view",
        "rules:read", "feedback:submit"
    },
    "admin": {
        "submission:create", "submission:read", "submission:delete", "analysis:run", "chat:use",
        "comparison:use", "dashboard:view", "knowledgebase:view", "rules:read", "feedback:submit",
        "rules:write", "rules:generate", "users:manage"
    },
    "super_admin": {
        "knowledgebase:view", "rules:read", "rules:write", "rules:generate", "feedback:submit",
        "console:view", "users:manage", "audit:view", "usage:view"
    },
}

def role_has(role: str, perm: str) -> bool:
    return perm in ROLE_PERMISSIONS.get(role, set())
