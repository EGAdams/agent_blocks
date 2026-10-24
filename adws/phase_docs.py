
RESOLVE_CATEGORY_DOC = """
# Resolve Category Phase

**Purpose:** Find and verify the user's category.

## How It Works
1. Read the requested category.
2. Find its database match.
3. Reject unclear matches. Never guess.
4. Collect the matching category IDs.
5. Log the results.
6. Return the verified category information.

## Dependencies
- **resolver:** Finds matching categories.
- **books:** Provides financial records.
- **request:** Contains the user's request.
- **logger:** Records phase activity.

## Returns
**CategoryScope:** Verified category, IDs, and children.

## Design
- Program to interfaces.
- Keep responsibilities separate.
- Let ICategoryResolver handle matching.
"""
