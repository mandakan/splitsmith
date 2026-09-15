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

Justification, paste as written. The "Request minimum scopes"
reviewer checks that no narrower scope would do, so the text must argue
that explicitly; a one-line description of the feature is rejected with
"does not sufficiently explain why the requested OAuth scopes are
necessary" (it was, on 2026-09-15). Add the demo video link and the
timestamps of the consent step and the Studio view once it is recorded.

> Splitsmith is a desktop and web application for IPSC sport shooters.
> It detects the timer beep and each shot in a head-camera recording,
> computes split times, and renders the match into an MP4 with the shot
> clock burned in. The feature that needs this scope is "Upload to
> YouTube": after rendering, the user connects their own YouTube channel
> and the app publishes the finished video to it, instead of the user
> re-uploading it by hand in YouTube Studio.
>
> An upload is more than a video file. Each upload makes these calls,
> all against the signed-in user's own channel:
> - videos.insert: the rendered MP4 with the title, description (chapter
>   timestamps per stage), tags and the privacy status the user chose.
> - captions.insert: a caption track the app generates with the split
>   time of every shot.
> - thumbnails.set: the thumbnail rendered from the video's title page.
> - playlists.list (mine=true), playlists.insert, playlistItems.insert:
>   so the user can file the video in one of their existing playlists or
>   a new one.
> - channels.list (mine=true): to show which channel is connected.
>
> Why youtube.force-ssl rather than a narrower scope: captions.insert is
> authorized only by youtube.force-ssl (or youtubepartner, which is for
> content owners and does not apply). youtube.upload authorizes
> videos.insert and thumbnails.set but neither captions nor playlists;
> youtube authorizes playlists but not captions. Requesting
> youtube.upload plus youtube would still not authorize the caption
> track, and would be two scopes where one suffices. youtube.force-ssl
> is the single scope that covers every call above, so it is the minimum
> for this feature.
>
> The app makes no other YouTube API calls. It never reads, lists or
> modifies videos, comments, subscriptions or any data on other users'
> channels; its only read calls are channels.list and playlists.list
> with mine=true. The refresh token is stored on the user's own machine
> and never on our servers; the user disconnects from within the app,
> and Google's third-party access page revokes access at any time. The
> demo video shows the consent screen, the upload, and the resulting
> caption track, thumbnail and playlist entry in YouTube Studio.

The justification field on the Data access page is capped at 1000
characters, which the block above exceeds. This one is 991 with the
blank line; paste it there and keep the long form for the audit form's
"API client description", which has no such cap:

> Splitsmith renders a shooter's match video with split times burned in, then publishes it to the user's own YouTube channel from the app. Each upload makes these calls, all on the signed-in user's channel: videos.insert (the MP4, title, description with chapters, privacy), captions.insert (a caption track with each shot's split time), thumbnails.set, playlists.list/insert and playlistItems.insert (file it in a playlist), channels.list mine=true (show the connected channel).
>
> Minimum scope: captions.insert is authorized only by youtube.force-ssl (youtubepartner is for content owners). youtube.upload covers the video and thumbnail but not captions or playlists; youtube covers playlists but not captions. Their combination would still not authorize captions and would be two scopes. force-ssl is the one scope covering every call. The app makes no other YouTube calls and never touches other users' data; the token is stored on the user's machine and revocable in the app or at Google.

The call list is the whole of `splitsmith/youtube/client.py` plus the
`channels.list` in `oauth.py`; if a call is added, add it here too, since
the reviewer compares the text against the demo recording.

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
