[Documentation](README.md) · [Italiano](USAGE.it.md)

# Using Dropwheel

## How to use it

There are five ways to open the wheel.

1. **Shift during a drag.** Start dragging, tap <kbd>Shift</kbd>, move to a segment and release. This
   is the main way. Dropwheel checks that a drag is really in progress, so Shift keeps working normally
   while you type.
2. **The hot corner.** Drag files onto the 6×6 pixel area in the top-right corner of the screen. The
   corner, size and even its presence are configurable.
3. **SUPER+ALT+D.** Copy files in your file manager and press the shortcut. With nothing on the
   clipboard, you get an empty target to drop files on.
4. **The file manager.** Right-click files and choose **Open in Dropwheel**.
5. **The terminal.** Run `dropwheel open photo.heic`.

**During a drag**

| Gesture | Effect |
|---|---|
| Tap <kbd>Shift</kbd> | Opens the wheel; when it is open, switches between the **Tools** and **File** pages |
| Rest on a group for 450 ms | Opens the ring of that group |
| Rest on the centre for 450 ms | Goes back to the previous ring |
| Drop on an action | Runs it, or opens its editor |
| Drop on a group | Keeps the wheel open on that ring, in click mode |
| Drop on the centre or outside | Cancels |
| <kbd>Esc</kbd> | Cancels the drag and closes the wheel |

**In click mode** (after SUPER+ALT+D, the hot corner, the file manager, or a drop on a group)

| Key or gesture | Effect |
|---|---|
| Arrows, <kbd>Tab</kbd>, mouse wheel | Move the highlight |
| <kbd>1</kbd> to <kbd>8</kbd> | Run segment N, clockwise from noon |
| <kbd>Enter</kbd> or click | Run the action or open the group |
| <kbd>Alt</kbd>+<kbd>Enter</kbd> or right-click | Set the parameters of a quick action first |
| <kbd>Backspace</kbd> or click on the centre | Go back |
| <kbd>Esc</kbd> | Go back; on the main ring, close |
| <kbd>Shift</kbd> or <kbd>Ctrl</kbd>+<kbd>Tab</kbd> | Switch page |

**Job cards**

After a drop, a card appears in the bottom-right corner. While running, it shows progress and a cancel
button. When finished, it shows a preview, the size saved and buttons to **Open**, **Show in folder**,
**Copy** and **Undo**. You can also **drag the result** straight into a browser, chat or email. Failed
jobs stay until you close them, with the details and, when a program is missing, a button that copies
the install command.

## Configuration

No configuration is needed. To start from a commented file at `~/.config/dropwheel/config.toml`:

```sh
dropwheel config --init
```

Changes apply the next time the wheel opens. An invalid value falls back to its default with a
warning, and never blocks anything. For example:

```toml
language = "en"                     # auto | it | en
hidden_actions = ["image.background.*"]

[output]
location = "source"                 # next to the original; "folder" = always the folder below
folder = "~/Downloads/Dropwheel"

[hotzone]
corner = "bottom-left"

[image]
jpeg_quality = 82

# Your own actions, shown in the wheel next to the built-in ones
[[custom]]
id = "blog-image"
label = "For the blog"
icon = "web"
categories = ["image"]
command = "magick {file} -resize '1600x1600>' -quality 80 {dir}/{stem}-blog.jpg"
output = "files"
```

Every key, with examples of custom actions, is documented in [docs/CONFIG.md](CONFIG.md).

## File manager integration

| File manager | How | In the menu |
|---|---|---|
| **Thunar** (Caelestia's default) | `dropwheel setup thunar --apply` | Open in Dropwheel |
| **Nautilus** | `dropwheel setup nautilus` | Scripts, then Dropwheel |
| **Dolphin** | Included in the package, or `dropwheel setup dolphin` | Open in Dropwheel |
| **Nemo** | Included in the package, or `dropwheel setup nemo` | Open in Dropwheel |

Caelestia manages Thunar's `uca.xml`, so Dropwheel only changes it when you pass `--apply`, and it
makes a backup first. See [integrations/thunar/README.md](../integrations/thunar/README.md) for the manual
steps.

---

← [Previous](INSTALL.md) · [Next](FEATURES.md) →
