PAGE CAPTURE
============

Page Capture turns a fixed rectangle of your screen into an ordered PDF.

QUICK START
-----------

1. Double-click "Launch Page Capture.cmd".
2. Click "Select Page Area" and drag around the page or text area. Every
   connected monitor is covered, and the selection may cross between monitors.
3. Choose either workflow:

   Manual (recommended)
   - Turn the page in the reading application.
   - Press F8 after each page becomes visible.
   - A sound confirms each successful capture.
   - F8 is watched globally, so Page Capture can remain hidden while the reading
     application is focused.

   Automatic
   - Press F10 to start automatic capture.
   - The current page is captured first.
   - Turn pages normally. A new page is saved after the selected area stops changing.
   - Press F10 again to stop and return to the toolbar.
   - Automatic detection is best-effort. Review the page counter and use manual F8
     capture for readers with blank/loading frames or long animations.

4. Check the page counter.
5. Press F7 if the last page was wrong. It is moved into the recoverable
   "Undone" folder instead of being deleted.
6. Press F9, choose a filename, and save the PDF.
7. After a successful export, click "Reset to 0 Pages" to start the next PDF.
   The exported PDF and original PNG screenshots are preserved, and the selected
   page area remains ready for F8.
   If you capture or undo a page after exporting, press F9 again before Reset is
   re-enabled. This ensures the current page set has been exported first.

GLOBAL HOTKEYS
--------------

F6  Select a new capture area
F7  Undo the most recent page
F8  Capture the current page
F9  Finish and create the PDF
F10 Start or stop automatic capture

If the keyboard uses F8 as a brightness, volume, or media key, press Fn+F8 or
enable Fn Lock so Windows receives a real F8 key. The reading application may
also react to F8; Page Capture does not block the key from the foreground app.

FILES AND RECOVERY
------------------

The original lossless PNG screenshots are kept in:

Documents\Page Capture Sessions\<date_and_time>

The PDF is saved wherever you choose. Undo never permanently deletes a page.
Reset starts a new timestamped session and never deletes the previous session.

CAPTURED-PAGE GALLERY
---------------------

The toolbar shows several numbered thumbnails at once. Scroll the thumbnail
strip horizontally to review earlier pages. Undo removes the latest thumbnail
at the same time it moves that PNG into the recoverable Undone folder.

PDF QUALITY
-----------

Leave "Photo/compact PDF" unchecked for the sharpest text. Enable it for a
smaller file when the source contains mostly photographs or the PDF will have
many pages.

TIPS
----

- Use manual F8 capture for the most reliable results.
- Page Capture supports monitors positioned above, below, left, or right of the
  primary display, including a page or window stretched across two monitors.
- If Windows rearranges, rotates, connects, or disconnects a display, press F6
  and select the area again. The app rejects stale coordinates rather than
  capturing the wrong part of the desktop.
- In automatic mode, keep the mouse pointer and animations outside the selected area.
- Increase the wait time if the application has a slow page-turn animation.
- Wait for the confirmation sound before pressing another Page Capture hotkey.
- Select only the document page, not browser controls, clocks, or video.
- Keep the capture resolution high enough that small text is readable.
- The PDF contains images. It will not be searchable unless OCR is added later.

RESPONSIBLE USE
---------------

Use Page Capture only for material you are allowed to save. The tool does not
bypass DRM, protected-video capture, application security, or screenshot blocks.

IF THE LAUNCHER DOES NOT OPEN
-----------------------------

The launcher checks for Python 3, Pillow, ReportLab, and Tkinter before opening
the app. If anything is missing, it leaves a visible explanation instead of
failing silently. Open this folder in Codex and ask it to repair Page Capture;
your existing screenshots and PDFs will remain untouched.
