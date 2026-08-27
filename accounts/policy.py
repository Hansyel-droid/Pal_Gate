"""
Campus Access Policy — version and acceptance helpers.

The policy text itself lives in templates/accounts/campus_policy.html, not
here: it is a document to be read, not data to be processed, and keeping it
in a template means it renders with the site's own styling and can be
corrected without a migration.

WHAT TO DO WHEN THE MEMORANDUM IS REVISED
-----------------------------------------
1. Update templates/accounts/campus_policy.html with the new text.
2. Bump CAMPUS_POLICY_VERSION below (e.g. '2026-03-23' -> '2027-01-15').

That second step is the one that matters. Bumping the version invalidates
every existing acceptance, so every applicant is shown the revised policy
and must accept it again before they can use the system. Editing the
template WITHOUT bumping the version silently changes what people are
held to after they already agreed — which is exactly the situation this
module exists to prevent.

The version string is the memorandum's own effectivity date, so the value
in the database is directly traceable to a specific document rather than
being an opaque counter.
"""

# Effectivity date of the memorandum currently in force.
# Source: Revised Campus Access Policy, Series of 2026, effective 23 March 2026.
CAMPUS_POLICY_VERSION = '2026-03-23'


def has_accepted_current_policy(user):
    """
    True if `user` has accepted the version of the policy currently in
    force. Anonymous users are never considered to have accepted.
    """
    if not user.is_authenticated:
        return False
    return user.policy_acceptances.filter(
        version=CAMPUS_POLICY_VERSION
    ).exists()
