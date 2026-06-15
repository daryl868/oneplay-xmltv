# OnePlay OCR XMLTV Starter

This repo captures screenshots from OnePlay M3U streams, crops the movie-title overlay, runs OCR with Tesseract, and generates `guide.xml`.

## Start

1. Upload all files to a private GitHub repo.
2. Go to Actions -> Generate XMLTV -> Run workflow.
3. The workflow starts with `MAX_CHANNELS: "1"` so only one channel is tested.
4. Check the generated `debug/*_crop_*.jpg` images.
5. If the crop hits the title text, change `MAX_CHANNELS` to `0` in `.github/workflows/xmltv.yml`.

## XMLTV URL

After the workflow commits `guide.xml`, use:

`https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/guide.xml`

## Crop setting

Default crop:

`crop=900:220:80:500,scale=1800:-1`

Format:

`crop=width:height:x:y`

Adjust it if the debug crop image is not showing the title overlay.

## Important

Your M3U contains IPTV credentials. Keep the repo private or rotate the credentials before making anything public.
