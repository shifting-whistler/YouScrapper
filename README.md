# YouScraper

YouScraper is a local Windows desktop application for extracting, organizing, searching, and exporting public YouTube metadata. It supports channel archives and multi-source playlist organization without downloading video files.

## Download

<p align="center">
  <a href="https://github.com/shifting-whistler/YouScrapper/releases/latest">
    <strong>Download for Windows</strong>
  </a>
  &nbsp;&nbsp;•&nbsp;&nbsp;
  <a href="https://github.com/shifting-whistler/YouScrapper/releases">
    <strong>View Releases</strong>
  </a>
</p>

Install the Windows setup package and launch YouScraper like a normal desktop application. A portable build is also available .

<img width="1343" height="680" alt="image" src="https://github.com/user-attachments/assets/e4b2834d-5d2e-4c11-85b8-e0ff7cde08be" />


## Features

- Extract public metadata from YouTube channels and playlists without a YouTube Data API key.
- Add multiple channels and direct playlist URLs in one Playlist Organizer session.
- Discover playlists from each source and select exactly which playlists to process.
- Preserve playlist identity and original playlist order.
- Reuse metadata when the same video appears in multiple selected playlists.
- Choose metadata fields before extraction.
- Search and sort extracted records while keeping the full dataset in one scrollable preview.
- Export the current result scope as UTF-8 TXT, Excel-friendly CSV, or landscape PDF.
- Keep playlist and source grouping in playlist-mode exports.
- Handle multilingual text and Unicode metadata.
- Run entirely locally; extracted data is not uploaded to a cloud backend.

## Screenshots


<img width="1366" height="720" alt="image" src="https://github.com/user-attachments/assets/b9b6694c-5e08-4211-aa7f-19acdf7f44a8" />


<img width="1366" height="720" alt="image" src="https://github.com/user-attachments/assets/7e4eee31-d82a-4e09-a3b0-77807c18f26b" />


<img width="1366" height="720" alt="image" src="https://github.com/user-attachments/assets/c7d616f9-d161-4cd3-8f2f-23f14205bc80" />


<img width="1366" height="720" alt="image" src="https://github.com/user-attachments/assets/23891e00-db16-4e8b-9045-4de1aeffcec6" />



## Supported URLs

### Channels

- `https://www.youtube.com/@channelname`
- `https://www.youtube.com/channel/UC...`
- `https://www.youtube.com/c/...`
- `https://www.youtube.com/user/...`

### Playlists

- `https://www.youtube.com/playlist?list=...`

## How it works

YouScraper uses an Electron desktop shell with a bundled Python extraction engine based on yt-dlp. The Windows release bundles the Python runtime and required dependencies, so ordinary users do not need to install Python, Node.js, npm, yt-dlp, Deno, or other development tools.

The application retrieves publicly accessible YouTube metadata over the internet and keeps the resulting dataset on the local computer. It does not download the actual video files and does not attempt to bypass authentication or access restrictions.

## Exports

**TXT**

Readable UTF-8 metadata blocks. Playlist mode is grouped by source and playlist.

**CSV**

Standards-compliant UTF-8 CSV with proper escaping for commas, quotes, multiline descriptions, and Unicode. Playlist mode includes source and playlist columns.

**PDF**

Landscape selectable-text PDF with automatic page flow, repeated headers, wrapped long text, and Unicode font support. Playlist mode is grouped by playlist.

## System requirements

For normal users, the Windows installer or portable release is self-contained. No separate Python, Node.js, Rust, browser, or package installation is required.

## Limitations

YouTube can change its public site, request flow, anti-automation measures, or rate limits. YouScraper therefore depends on the actively maintained yt-dlp extraction implementation. Some optional metadata, such as like counts, may be unavailable for individual videos and will remain blank rather than being estimated. Very large archives can take time because detailed metadata may require additional video-level requests.

## Development

Clone or download the repository, then use `DEV.bat` on Windows for development. This launches the Electron desktop shell and Python backend without producing an installer.

For a Windows release build, run `build\build_windows.bat` on Windows. The build process produces the installer and portable executable from the bundled application configuration.

Developer build notes for release packaging are intentionally kept in `PRIVATE_BUILD_NOTES.txt` and are excluded from source-control tracking.

## Release files

The GitHub Releases page should contain the Windows installer and, when published, the portable executable. Replace the placeholder release link above with the actual latest-release URL.

Recommended release assets:

- `YouScraper-Setup.exe`
- `YouScraper-1.0.0-Portable.exe`

## Credits

YouScraper was made by Tanvir Mahtab (aka shifting whistler).

## License

YouScraper is released under the MIT License. See [LICENSE](LICENSE).

