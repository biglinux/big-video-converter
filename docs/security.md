# Security decisions

## Trust boundaries

Imported presets, profiles, pasted text and the media files a user opens are
untrusted. Bounded input still has to be processed in linear time on the GTK
thread, and settings that reach files or the network need confirmation.

The repository checkout is trusted. The scripts in `tools/` run on the
maintainer's own checkout, catalogs and translation JSON; a symlink, file
name or catalog that reaches them came through code review, where it could
as well have changed the scripts. They are not hardened against hostile
input.

FFmpeg confines a manifest opened from a local file (DASH, HLS) to the
`file`, `crypto` and `data` protocols and refuses HLS under an unusual
extension, so probing a local file does not reach the network. The
application relies on that and adds no protocol whitelist of its own.

## Accepted risks

### Playlist inputs read files beside them

FFmpeg detects an input by its content, not its extension, so an
`ffconcat` playlist named `video.mp4` is converted as the media it lists.
The concat demuxer's default safe mode limits those entries to relative
paths below the playlist's own folder, and the result is written to the
user's chosen output folder.

Inputs are not restricted by detected format. Exploiting this needs a
deliberately crafted file placed next to the target media, and it can only
copy media the user can already read into a file the user owns. Anyone
able to plant that file and collect the output already has a wider reach
than this gives them.
