# Signing in, registering, and signing out

Browsing the catalogue needs no account. You only need to sign in to do something the
catalogue records against you — proposing a change, registering implementer interest,
commenting, or reviewing.

## Creating an account

Choose **Register**. You are taken to the NPTC sign-in service, where you create an
account with a username, an email address and a password. This is an ordinary
registration form: the catalogue application itself never sees your password.

Once you have registered you are returned to the catalogue, already signed in. A new
account starts as a **Provisional** member; an administrator can grant further roles
later.

> **Not yet implemented.** The privacy notice and terms of use are not currently
> presented for acceptance during registration. See the follow-up issue linked from
> [ADR-0021](../adr/0021-browser-side-pkce-login.md).

## Signing in

Choose **Sign in**, or try to open a page that needs an account — you will be sent to
sign in and returned to the page you were heading for once you are done.

If you have signed in recently, this may complete without showing you a form at all: the
sign-in service remembers your session for a while, and the catalogue quietly re-uses it.

## Multi-factor authentication

Ordinary use needs only your username and password. **Administrators** must additionally
confirm a one-time code from an authenticator app before performing administrative
actions. You will be prompted to set this up the first time it is required.

If you hold the Administrator role but have not completed that second step, administrative
actions are refused and you are prompted to sign in again and complete it. Everything
your other roles allow continues to work as normal.

## Signing out

Choose **Sign out**. This ends your session both in the catalogue and at the sign-in
service, so returning to the sign-in page asks for your password again rather than
silently resuming.

Note that signing out does not close the session on other devices you may be signed in on.

## If something goes wrong

**"Sign-in could not be completed."** The sign-in link was reused, opened in a different
tab from the one that started it, or left too long before finishing. Start again from the
sign-in page. This is a safety check, not a fault: it is what stops a sign-in link being
replayed by someone else.

**"Sign-in is unavailable."** The catalogue cannot reach the sign-in service. This is
usually temporary — try again in a few minutes. The public catalogue stays available
meanwhile.

**You are asked to sign in again unexpectedly.** Sessions expire after a period of
inactivity. Sign in again to continue; nothing you have already submitted is lost.
