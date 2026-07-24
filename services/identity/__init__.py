# Clerk owns the credential — placeholder satisfies NOT-NULL on `users.hashed_password`
# without ever being a valid bcrypt input. Extracted here to keep clerk_provision.py
# and webhooks_clerk.py in lockstep.
CLERK_PLACEHOLDER_HASH = "$2b$12$ClerkOwnsThisPasswordPlaceholderHashXXXX"
