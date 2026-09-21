# Audio for Friday feature Reels

Drop a music file here (`.mp3`, `.m4a`, `.aac`, `.wav`, `.ogg`) and
`reel.py --feature ... --audio <name>` will mux it into the video. With no
file here, Reels are built with a silent track, exactly as they were before.

**Nothing is bundled.** This folder ships empty on purpose.

## The thing to understand before you add a track

Instagram's own audio library — the trending sounds — is **not reachable
through the Content Publishing API**, and a Reel's audio cannot be changed
after it is published. So a Reel that this repo publishes automatically can
only carry audio that is already inside the file.

That leaves two routes, and they are genuinely different trades:

**1. Automated, with a track from this folder.** Zero touch: the Friday Reel
builds, commits and publishes on its own. The music has to be something you
are entitled to publish — CC0, or a licence you hold. Good sources:

  - [Free Music Archive](https://freemusicarchive.org) (filter to CC0 / CC BY)
  - [Pixabay Music](https://pixabay.com/music/) (Pixabay licence, free for commercial use)
  - [Incompetech](https://incompetech.com) (CC BY — attribution required)
  - [YouTube Audio Library](https://www.youtube.com/audiolibrary) (check per-track terms)

If the licence needs attribution, put it in the post caption, and keep a note
of it next to the file here.

**2. By hand, with a trending sound.** The pipeline still builds the video and
commits it; you download it and post it from the phone, picking a trending
sound in the app. Costs you about two minutes. Trending audio is itself a
ranking signal on Reels, so this route often gets more reach than the
automated one — which is why the build deliberately produces a file that is
useful either way rather than assuming you will publish it automatically.

## Do not

Do not drop in a commercial track you do not have rights to. The account
publishes publicly under your name; a copyright strike lands on you, not on
this repo. If you are unsure about a file's licence, that uncertainty is the
answer — use route 2 instead.
