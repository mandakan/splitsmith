# Google OAuth verification kit

What the splitsmith Google Cloud project needs to leave "Testing" status
(consent-screen brand verification) and to lift YouTube's private-only
lock on unaudited projects (the YouTube API Services audit). The code
does not wait on either; until both pass, uploads land private and a
test user's refresh token expires after seven days.

## Consent screen (Google Auth Platform > Branding)

| Field | Value |
|---|---|
| App name | `Splitsmith` |
| User support email | the address on the project (yours) |
| App logo | `logo-120.png` in this folder (120 x 120 PNG, the brand mark on the panel colour; `logo-512.png` is the same at 512 for anywhere that wants it larger) |
| Application home page | `https://splitsmith.app` |
| Application privacy policy link | `https://splitsmith.app/privacy` |
| Application terms of service link | `https://splitsmith.app/terms` |
| Authorized domains | `splitsmith.app` |
| Developer contact | the same address |

Uploading the logo is what triggers brand verification; that is
intended, since the audit needs the app published anyway.

## Scopes (Google Auth Platform > Data access)

One scope: `https://www.googleapis.com/auth/youtube.force-ssl`
(sensitive, not restricted; no third-party security assessment).

Justification, paste as written:

> Splitsmith is a desktop and web tool for IPSC sport shooters. It reads
> the timer beep and every shot out of a head-camera recording and turns
> them into split times, then renders the match into a video with the
> shot clock burned in. This scope lets the user publish that finished
> video to their own YouTube channel from inside the app instead of
> re-uploading it by hand in YouTube Studio. The app uses the scope for
> exactly these calls, all on the signed-in user's own channel:
> `videos.insert` to upload the rendered video with the title,
> description (including chapter timestamps) and tags the app
> generated; `captions.insert` to attach the per-shot caption track;
> `thumbnails.set` to set the thumbnail rendered from the title page;
> `playlists.list`, `playlists.insert` and `playlistItems.insert` so the
> user can file the video into one of their playlists; and
> `channels.list (mine=true)` to show which channel is connected. It
> reads no other channel data and never acts on another user's content.
> The refresh token is stored on the user's own machine; the user
> disconnects with one command or from the app, and Google's third-party
> access page revokes it at any time.

Why not `youtube.upload` alone: it does not cover `captions.insert`,
`thumbnails.set` or the playlist calls, and the reviewer will ask why a
second scope is requested if both are listed. `force-ssl` covers all of
them.

## Demo video (brand verification and the audit both ask for one)

An unlisted YouTube video, screen recording, no audio needed, under
three minutes. Record in this order, at 1080p, with the browser's
address bar visible on the consent step so the reviewer can see the
client id and scope:

1. `splitsmith.app` in the browser: the home page, then scroll to the
   Outputs section (the YouTube line), then open `/privacy` and scroll
   to the YouTube section.
2. The desktop app's Export page for a match, MP4 output, YouTube on.
   Click **Connect YouTube**.
3. Google's consent page: the account picker, the scope description,
   **Allow**. The "YouTube is connected" tab. Back in the app: "Connected
   as <channel>".
4. Pick a playlist, choose Unlisted, **Export bundle**. Let the progress
   run through "Uploading ... MB of ... MB" to "Uploaded youtu.be/...".
5. YouTube Studio: the uploaded video with its title, description with
   chapters, the caption track under Subtitles, the thumbnail, and the
   playlist.
6. Back in the app: the row menu, **Disconnect**. Then
   `myaccount.google.com/permissions` showing Splitsmith listed (or
   already gone).

The same recording serves the audit form's "demonstrate how your
application uses the API" item.

## YouTube API Services audit (the private-lock lift)

Form: "YouTube API Services - Audit and Quota Extension Form", linked
from the Quota and Compliance Audits guide. Answers to keep on hand:

- Project number: from the Cloud console Dashboard.
- API client description: the scope justification above, plus "open
  source, MIT, github.com/mandakan/splitsmith".
- Quota: no increase requested; the default 100 uploads/day is enough.
- Where users are told about YouTube: `https://splitsmith.app/privacy`,
  YouTube section; it names YouTube API Services, links the Google
  Privacy Policy and the YouTube Terms of Service, lists what is stored
  (refresh token on the user's machine, video id in the export's
  sidecar), and how to revoke.
- Data retention: the refresh token until the user disconnects; no
  YouTube data other than the uploaded video's id and the chosen
  playlist's id and title, kept next to the export.
- Required minimum functionality checklist: uploads are the user's own
  content to the user's own channel; the app never displays or modifies
  other users' YouTube data; the app's terms link YouTube's.

## After both pass

Nothing in the code changes. Refresh tokens stop expiring weekly and
uploads honour the privacy the user chose.
