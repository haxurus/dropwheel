[Documentation](README.md) · [Italiano](PRIVACY.it.md)

# Privacy and security

- **Everything is local.** There is no telemetry, cloud service or account.
- **The network is used only when you ask:**
  - *QR code for your phone* starts a temporary HTTP server on the local network, on a random port and
    address, which serves only the chosen files and stops after 15 minutes or when you close the card.
  - *KDE Connect and LocalSend* send to your paired devices.
  - *Dropped links* are downloaded only from that link's site.
  - *AI models* are downloaded only when you install or first use the feature.
  - *On Windows, macOS and GNOME*, `dropwheel setup windows|macos|gnome` downloads the icon font once, from a fixed version on GitHub,
    and checks its SHA-256 before using it.
- **On GNOME, Dropwheel's Shell extension** answers one question on your session bus: where the pointer is
  and which modifier keys and mouse buttons are held right now. It records nothing and sends nothing.
- **On Windows, macOS and GNOME the CLI talks to the interface only on `127.0.0.1`**, with a token that changes at every
  start and is stored in your own cache folder. Dropwheel reads the state of Shift and of the mouse
  buttons; it installs no keyboard hook and records no keys. On macOS the Finder Quick Action and the
  LaunchAgent written by `dropwheel setup macos` only start Dropwheel, and the clipboard and the trash go
  through macOS itself (`pbcopy`, `osascript`), without Apple Events to other apps.
- **Nothing you drop is executed.** Document macros and PDF JavaScript never run, an AppImage is
  inspected without starting it, and remote images in Markdown and external references in SVG are not
  fetched.
- **Untrusted files are handled defensively.** Dangerous archive entries, such as absolute paths, `..`
  and links pointing outside the archive, are rejected, and there are guards against archive and
  document bombs.
- **Passwords** stay in memory only for the job that uses them.

When redacting images, blur and pixelate can be reversible on text. Use a solid box for sensitive data.

---

← [Previous](TROUBLESHOOTING.md) · [Next](DEVELOPMENT.md) →
