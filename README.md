# thechrisabrahamshow.com

A static mirror of The Chris Abraham Show's Spotify for Creators feed, one page
per episode, in the style of hillmole.com.

    pip install pillow
    python3 build.py            # fetch the feed, cache art and transcripts, write the site
    python3 build.py --offline  # rebuild from data/episodes.json

`data/episodes.json` is the record: each episode keeps its first address
forever. Artwork lives in `assets/images/`, transcripts in `data/transcripts/`.
Audio is never downloaded. `.github/workflows/update.yml` runs the build daily.
