"""
Texture Manager for Mari
Dockable texture browser: folder tree, cached thumbnails, loading into the Image Manager.
Works with PySide6 (Mari 8) and PySide2 (Mari 7.x).
"""
import os
import time
import traceback
from collections import deque
import mari

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui


PALETTE_NAME = "Texture Manager"

THUMB_SIZE = 128

# Qt 6 rejects images above 256 MB by default (e.g. 8K PNGs)
MAX_IMAGE_MB = 2048

CACHE_FOLDER_NAME = "TM_Cache"

# Delay before uncached thumbnails are decoded, so leaving a folder quickly never freezes the UI
UNCACHED_DELAY_MS = 700

# Folder search: subfolders are indexed in small time slices so the UI never freezes
INDEX_TIME_BUDGET_MS = 20
INDEX_TICK_MS = 10
MAX_FOLDER_MATCHES = 300

# Settings file (portable, can be copied between machines)
SETTINGS_FILE = os.path.expanduser("~/.mari_texture_manager/settings.ini")

# Show the palette when Mari starts. Set to False if you only want it via View > Palettes.
SHOW_ON_STARTUP = True

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".tga", ".bmp",
                    ".exr", ".tx", ".hdr")

PATH_ROLE = QtCore.Qt.ItemDataRole.UserRole

# Keep a global reference so the widget is not garbage collected (survives re-running in the console)
try:
    _widget
except NameError:
    _widget = None


def log(message, error=False):
    """Writes to the Python console and the Mari log."""
    text = "TEXTURE MANAGER: " + message
    print(text)
    try:
        mari.app.log(text)
    except Exception:
        pass
    if error:
        traceback.print_exc()


def normalize_path(path):
    path = path.strip().strip('"').strip("'")
    if not path:
        return ""
    path = os.path.normpath(os.path.expanduser(path))
    return path.replace("\\", "/")


def child_path(folder, name):
    """Joins without producing 'X://name' when the folder is a drive root like 'X:/'."""
    return folder + name if folder.endswith("/") else folder + "/" + name


def relative_parts(path, root):
    """['a', 'b'] for path 'X:/root/a/b' below root 'X:/root'. None if path is not below root."""
    prefix = root if root.endswith("/") else root + "/"
    if not path.startswith(prefix) or path == prefix:
        return None
    return path[len(prefix):].split("/")


def to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_settings():
    """INI file in the user's home. Migrates settings from the old registry/native location once."""
    settings = QtCore.QSettings(SETTINGS_FILE, QtCore.QSettings.Format.IniFormat)
    if not os.path.isfile(SETTINGS_FILE):
        old = QtCore.QSettings("TextureManager", "MariTextureManager")
        for key in old.allKeys():
            settings.setValue(key, old.value(key))
        settings.sync()
    return settings


def get_qt_formats():
    """File extensions Qt can read in this Mari version."""
    formats = set()
    for fmt in QtGui.QImageReader.supportedImageFormats():
        try:
            name = fmt.data()
            if isinstance(name, bytes):
                name = name.decode("ascii")
            formats.add("." + str(name).lower())
        except Exception:
            pass
    return formats


def get_cache_path(path):
    folder, name = os.path.split(path)
    return child_path(child_path(folder, CACHE_FOLDER_NAME), "{}.thumb{}.jpg".format(name, THUMB_SIZE))


def is_cache_valid(path):
    cache_path = get_cache_path(path)
    try:
        return os.path.isfile(cache_path) and os.path.getmtime(cache_path) >= os.path.getmtime(path)
    except OSError:
        return False


# Cache folders that could not be written (e.g. read-only shares), reported once each
_cache_failed_dirs = set()


def save_thumbnail_cache(image, cache_path):
    cache_dir = os.path.dirname(cache_path)
    if cache_dir in _cache_failed_dirs:
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        if not image.convertToFormat(QtGui.QImage.Format.Format_RGB888).save(cache_path, "JPG", 85):
            raise OSError("QImage.save failed")
    except Exception as exc:
        _cache_failed_dirs.add(cache_dir)
        log("Could not write cache to {} ({}). Thumbnails there will not be cached.".format(cache_dir, exc))


def load_thumbnail(path):
    """Returns (QImage or None, width, height). Uses the cache if valid, otherwise decodes and caches."""
    reader = QtGui.QImageReader(path)
    reader.setAutoTransform(True)

    size = reader.size()
    width, height = size.width(), size.height()

    cache_path = get_cache_path(path)
    if is_cache_valid(path):
        cached = QtGui.QImage(cache_path)
        if not cached.isNull():
            return cached, width, height

    if size.isValid():
        reader.setScaledSize(size.scaled(THUMB_SIZE, THUMB_SIZE,
                                         QtCore.Qt.AspectRatioMode.KeepAspectRatio))

    # Raise Qt's allocation limit only while reading, then restore it
    old_limit = None
    try:
        old_limit = QtGui.QImageReader.allocationLimit()
        QtGui.QImageReader.setAllocationLimit(MAX_IMAGE_MB)
    except AttributeError:
        pass  # Qt 5 has no allocation limit

    try:
        image = reader.read()
    finally:
        if old_limit is not None:
            QtGui.QImageReader.setAllocationLimit(old_limit)

    if image.isNull():
        log("Preview failed for {}: {}".format(path, reader.errorString()))
        return None, width, height

    save_thumbnail_cache(image, cache_path)
    return image, width, height


def make_up_arrow_icon(color, size=16):
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)

    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    pen = QtGui.QPen(color)
    pen.setWidthF(size * 0.12)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    mid = size / 2.0
    top = size * 0.2
    bottom = size * 0.82
    wing = size * 0.3
    painter.drawLine(QtCore.QPointF(mid, top), QtCore.QPointF(mid, bottom))
    painter.drawLine(QtCore.QPointF(mid, top), QtCore.QPointF(mid - wing, top + wing))
    painter.drawLine(QtCore.QPointF(mid, top), QtCore.QPointF(mid + wing, top + wing))
    painter.end()

    return QtGui.QIcon(pixmap)


def make_plus_icon(color, size=16):
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)

    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    pen = QtGui.QPen(color)
    pen.setWidthF(size * 0.12)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)

    mid = size / 2.0
    margin = size * 0.18
    painter.drawLine(QtCore.QPointF(mid, margin), QtCore.QPointF(mid, size - margin))
    painter.drawLine(QtCore.QPointF(margin, mid), QtCore.QPointF(size - margin, mid))
    painter.end()

    return QtGui.QIcon(pixmap)


def make_happy_smiley_icon(color, size=THUMB_SIZE):
    """Placeholder icon for images without a preview."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)

    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    pen = QtGui.QPen(color)
    pen.setWidthF(size * 0.03)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)

    # Face
    radius = size * 0.2
    center = QtCore.QPointF(size / 2.0, size * 0.36)
    painter.drawEllipse(center, radius, radius)

    # Eyes
    painter.setBrush(color)
    eye_r = size * 0.022
    painter.drawEllipse(QtCore.QPointF(center.x() - radius * 0.38, center.y() - radius * 0.25), eye_r, eye_r)
    painter.drawEllipse(QtCore.QPointF(center.x() + radius * 0.38, center.y() - radius * 0.25), eye_r, eye_r)

    # Smile
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    mouth = QtCore.QRectF(center.x() - radius * 0.5, center.y() - radius * 0.2,
                          radius * 1.0, radius * 0.8)
    painter.drawArc(mouth, 200 * 16, 140 * 16)

    # Text
    font = painter.font()
    font.setPixelSize(max(9, int(size * 0.105)))
    painter.setFont(font)
    text_rect = QtCore.QRectF(0, center.y() + radius + size * 0.05, size, size * 0.3)
    painter.drawText(text_rect,
                     int(QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop),
                     "You look\nsplendid today!")
    painter.end()

    return QtGui.QIcon(pixmap)


def find_loaded_image(path):
    """Returns the Image Manager entry for this file if it is already loaded, else None."""
    try:
        for image in mari.images.list():
            if normalize_path(image.filePath()) == path:
                return image
    except Exception:
        pass
    return None


def load_image_into_manager(path):
    """Returns (image, already_loaded)."""
    existing = find_loaded_image(path)
    if existing is not None:
        return existing, True
    return mari.images.open(path), False


def create_image_group(name, images):
    """addToImageGroup creates the group if it does not exist yet."""
    try:
        mari.images.addToImageGroup(list(images), name)
        return True
    except Exception as exc:
        log("addToImageGroup failed: {}".format(exc), error=True)
        return False


def parse_keywords(text):
    """'grunge, metal' -> ['grunge', 'metal']"""
    return [k.strip().lower() for k in text.split(",") if k.strip()]


def matches_keywords(text, keywords):
    text = text.lower()
    return any(k in text for k in keywords)


class FolderFilterProxy(QtCore.QSortFilterProxyModel):
    """Hides the cache folder and hidden folders, and filters the tree by keywords.

    A folder is shown if its name, or the name of any of its parents below the root,
    matches a keyword. Recursive filtering keeps the parents of matching subfolders visible.
    Subfolders that Qt has not loaded yet are found by the folder index in LibraryTab,
    which loads and expands them."""

    def __init__(self, parent=None):
        super(FolderFilterProxy, self).__init__(parent)
        self.root_path = ""
        self.keywords = []
        self.setRecursiveFilteringEnabled(True)

    def set_keywords(self, keywords):
        self.keywords = keywords
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        name = model.fileName(index)
        if name == CACHE_FOLDER_NAME or name.startswith("."):
            return False

        if not self.keywords or not self.root_path:
            return True

        # The root and its parents (drive etc.) must stay visible, otherwise the tree is empty
        parts = relative_parts(normalize_path(model.filePath(index)), self.root_path)
        if parts is None:
            return True
        return any(matches_keywords(part, self.keywords) for part in parts)


class LibraryTab(QtWidgets.QWidget):
    """One library: path field, search, folder tree and image grid. Each tab is independent."""

    def __init__(self, tab_id, parent=None):
        super(LibraryTab, self).__init__(parent)

        self.tab_id = tab_id
        self.root_changed_callback = None   # set by the tab container to update the tab title
        self.current_path = ""
        self.root_path = ""
        self.settings = get_settings()

        # Thumbnails are loaded one per timer tick on the main thread
        self.generation = 0          # incremented on every folder change, drops stale results
        self.items_by_path = {}
        self.thumb_queue = []        # (generation, path)
        self.uncached_queue = []     # (generation, path)
        self.qt_formats = get_qt_formats()

        self.thumb_timer = QtCore.QTimer(self)
        self.thumb_timer.setInterval(0)
        self.thumb_timer.timeout.connect(self.process_thumbnail_queue)

        self.delay_timer = QtCore.QTimer(self)
        self.delay_timer.setSingleShot(True)
        self.delay_timer.setInterval(UNCACHED_DELAY_MS)
        self.delay_timer.timeout.connect(self.start_uncached_thumbnails)

        # ---------------- Layout ----------------
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)

        path_layout = QtWidgets.QHBoxLayout()

        self.up_btn = QtWidgets.QPushButton()
        self.up_btn.setIcon(make_up_arrow_icon(self.palette().color(QtGui.QPalette.ColorRole.ButtonText)))
        self.up_btn.setToolTip("Up one folder")
        self.up_btn.setFixedWidth(30)

        self.path_field = QtWidgets.QLineEdit()
        self.path_field.setPlaceholderText("Enter a path and press Enter ...")

        self.browse_btn = QtWidgets.QPushButton("...")
        self.browse_btn.setToolTip("Browse for folder")
        self.browse_btn.setFixedWidth(30)

        path_layout.addWidget(self.up_btn)
        path_layout.addWidget(self.path_field)
        path_layout.addWidget(self.browse_btn)

        # Search field, filters the folder tree
        self.search_field = QtWidgets.QLineEdit()
        self.search_field.setPlaceholderText("Filter folders ... (comma = multiple keywords)")
        self.search_field.setClearButtonEnabled(True)
        self.keywords = []
        self.base_status = ""

        # Filter shortly after typing stops, not on every key press
        self.search_timer = QtCore.QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self.apply_filter)

        # Folder index for searching subfolders (built once per root, in time slices)
        self.folder_index = []        # all subfolder paths below the root
        self.index_queue = deque()    # folders still to scan
        self.index_done = False
        self.matched_folders = set()
        self.index_visited = set()    # real paths, protects against symlink loops

        self.index_timer = QtCore.QTimer(self)
        self.index_timer.setInterval(INDEX_TICK_MS)
        self.index_timer.timeout.connect(self.process_index_queue)

        icon_provider = QtWidgets.QFileIconProvider()
        self.file_icon = icon_provider.icon(QtWidgets.QFileIconProvider.IconType.File)
        self.no_preview_icon = make_happy_smiley_icon(self.palette().color(QtGui.QPalette.ColorRole.Mid))

        # Folder tree (QFileSystemModel reads directories in the background)
        self.fs_model = QtWidgets.QFileSystemModel(self)
        self.fs_model.setFilter(QtCore.QDir.Filter.Dirs | QtCore.QDir.Filter.NoDotAndDotDot)
        self.folder_proxy = FolderFilterProxy(self)
        self.folder_proxy.setSourceModel(self.fs_model)

        self.folder_tree = QtWidgets.QTreeView()
        self.folder_tree.setModel(self.folder_proxy)
        self.folder_tree.setHeaderHidden(True)
        for column in range(1, self.fs_model.columnCount()):
            self.folder_tree.hideColumn(column)
        self.folder_tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)

        # Image grid
        self.image_list = QtWidgets.QListWidget()
        self.image_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.image_list.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.image_list.setIconSize(QtCore.QSize(THUMB_SIZE, THUMB_SIZE))
        self.image_list.setGridSize(QtCore.QSize(THUMB_SIZE + 24, THUMB_SIZE + 28))
        self.image_list.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.image_list.setMovement(QtWidgets.QListView.Movement.Static)
        self.image_list.setTextElideMode(QtCore.Qt.TextElideMode.ElideLeft)
        self.image_list.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.folder_tree)
        self.splitter.addWidget(self.image_list)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 3)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                                        QtWidgets.QSizePolicy.Policy.Fixed)

        main_layout.addLayout(path_layout)
        main_layout.addWidget(self.search_field)
        main_layout.addWidget(self.splitter, 1)
        main_layout.addWidget(self.status_label, 0)

        # ---------------- Signals ----------------
        self.path_field.returnPressed.connect(self.on_path_entered)
        self.search_field.textChanged.connect(lambda _text: self.search_timer.start())
        self.search_field.returnPressed.connect(self.apply_filter)
        self.browse_btn.clicked.connect(self.on_browse)
        self.up_btn.clicked.connect(self.on_up)
        self.folder_tree.selectionModel().currentChanged.connect(self.on_tree_current_changed)
        self.image_list.customContextMenuRequested.connect(self.on_image_context_menu)
        self.image_list.itemDoubleClicked.connect(self.on_image_double_clicked)

        # ---------------- Restore last location ----------------
        last_root = self.settings.value(self.key("last_root"), "")
        last_path = self.settings.value(self.key("last_path"), "")
        if not last_root and not last_path and self.tab_id == "0":
            # Settings from the version before tabs
            last_root = self.settings.value("last_root", "")
            last_path = self.settings.value("last_path", "")
        if last_root and os.path.isdir(last_root):
            self.set_path(last_root)
            last_path = normalize_path(last_path)
            if last_path and os.path.isdir(last_path) and relative_parts(last_path, self.root_path):
                self.select_in_tree(last_path)
        elif last_path and os.path.isdir(last_path):
            self.set_path(last_path)
        else:
            self.set_status("No path set.")
            self.up_btn.setEnabled(False)

    # ---------------- Navigation ----------------

    def set_path(self, path):
        """Makes the folder the tree root and shows its images."""
        path = normalize_path(path)
        if not path:
            return
        if not os.path.isdir(path):
            self.set_status("Path does not exist: " + path, error=True)
            return

        self.set_root(path)
        self.show_folder(path)

    def set_root(self, path):
        self.root_path = path
        self.folder_proxy.root_path = path
        self.folder_proxy.invalidateFilter()
        self.settings.setValue(self.key("last_root"), path)
        source_index = self.fs_model.setRootPath(path)
        self.folder_tree.setRootIndex(self.folder_proxy.mapFromSource(source_index))
        self.folder_tree.clearSelection()
        self.reset_folder_index()
        if self.keywords:
            self.apply_filter()
        if self.root_changed_callback:
            self.root_changed_callback(self)

    def show_folder(self, path):
        """Shows a folder's images in the grid, the tree stays as it is."""
        self.current_path = path
        self.path_field.setText(path)
        self.settings.setValue(self.key("last_path"), path)

        parent = normalize_path(os.path.dirname(path))
        self.up_btn.setEnabled(bool(parent) and parent != path)

        self.populate_images()

    def select_in_tree(self, path):
        """Selects a folder in the tree, which triggers show_folder via the selection signal."""
        source_index = self.fs_model.index(path)
        proxy_index = self.folder_proxy.mapFromSource(source_index)
        if proxy_index.isValid():
            self.folder_tree.setCurrentIndex(proxy_index)
            self.folder_tree.scrollTo(proxy_index)
        else:
            self.show_folder(path)

    def populate_images(self):
        self.generation += 1
        self.thumb_queue = []
        self.thumb_timer.stop()
        self.uncached_queue = []
        self.delay_timer.stop()
        self.items_by_path = {}
        self.image_list.clear()

        images = []
        try:
            with os.scandir(self.current_path) as entries:
                for e in entries:
                    if e.name.startswith(".") or e.name == CACHE_FOLDER_NAME:
                        continue
                    if e.is_file() and os.path.splitext(e.name)[1].lower() in IMAGE_EXTENSIONS:
                        images.append(e.name)
        except PermissionError:
            self.set_status("No permission for: " + self.current_path, error=True)
            return
        except OSError as exc:
            self.set_status("Read error: " + str(exc), error=True)
            return

        images.sort(key=lambda name: name.lower())

        no_preview = 0
        for name in images:
            path = child_path(self.current_path, name)
            item = QtWidgets.QListWidgetItem(self.file_icon, name)
            item.setData(PATH_ROLE, path)
            item.setToolTip(path)
            # Fixed size so items are never clipped before their thumbnail arrives
            item.setSizeHint(QtCore.QSize(THUMB_SIZE + 24, THUMB_SIZE + 28))
            self.image_list.addItem(item)

            ext = os.path.splitext(name)[1].lower()
            if ext in self.qt_formats:
                self.items_by_path[path] = item
                # Cached thumbnails first, expensive originals after a short delay
                if is_cache_valid(path):
                    self.thumb_queue.append((self.generation, path))
                else:
                    self.uncached_queue.append((self.generation, path))
            else:
                no_preview += 1
                item.setIcon(self.no_preview_icon)
                item.setToolTip(path + "\nNo preview: format not supported by Qt")

        if self.thumb_queue:
            self.thumb_timer.start()
        if self.uncached_queue:
            self.delay_timer.start()

        status = "{} images".format(len(images))
        if no_preview:
            status += " ({} without preview)".format(no_preview)
        self.base_status = status
        self.update_status()

    # ---------------- Search ----------------

    def apply_filter(self):
        self.search_timer.stop()
        had_keywords = bool(self.keywords)
        self.keywords = parse_keywords(self.search_field.text())
        self.folder_proxy.set_keywords(self.keywords)
        self.matched_folders = set()

        if self.keywords:
            self.folder_tree.collapseAll()
            # Check the folders indexed so far, then keep indexing the rest in the background
            for path in self.folder_index:
                self.check_folder_match(path)
            if not self.index_done:
                self.index_timer.start()
        elif had_keywords:
            # Search cleared -> collapse everything again
            self.folder_tree.collapseAll()

        self.update_status()

    def update_status(self):
        parts = [self.base_status]
        if self.keywords:
            folders = "{} folder matches".format(len(self.matched_folders))
            if len(self.matched_folders) >= MAX_FOLDER_MATCHES:
                folders += " (limit reached)"
            if not self.index_done:
                folders += ", searching subfolders ({} scanned) ...".format(len(self.folder_index))
            parts.append(folders)
        self.set_status(" | ".join(p for p in parts if p))

    # ---------------- Folder index (subfolder search) ----------------

    def reset_folder_index(self):
        self.index_timer.stop()
        self.folder_index = []
        self.index_queue = deque([self.root_path]) if self.root_path else deque()
        self.index_done = not self.root_path
        self.matched_folders = set()
        self.index_visited = set()

    def process_index_queue(self):
        """Scans folders for a few milliseconds per timer tick, so the UI stays responsive."""
        start = time.monotonic()
        budget = INDEX_TIME_BUDGET_MS / 1000.0

        while self.index_queue and time.monotonic() - start < budget:
            folder = self.index_queue.popleft()
            try:
                real = os.path.realpath(folder)
                if real in self.index_visited:
                    continue  # symlink pointing to a folder we already scanned
                self.index_visited.add(real)
                with os.scandir(folder) as entries:
                    # Follows symlinks, like the tree and the image grid do
                    subfolders = [e.name for e in entries
                                  if e.is_dir()
                                  and e.name != CACHE_FOLDER_NAME
                                  and not e.name.startswith(".")]
            except OSError:
                continue

            for name in sorted(subfolders, key=lambda n: n.lower()):
                path = child_path(folder, name)
                self.folder_index.append(path)
                self.index_queue.append(path)
                if self.keywords:
                    self.check_folder_match(path)

        if not self.index_queue:
            self.index_done = True
            self.index_timer.stop()

        if not self.keywords or len(self.matched_folders) >= MAX_FOLDER_MATCHES:
            # Nobody is searching any more, or enough matches: pause until the next search
            self.index_timer.stop()

        if self.keywords:
            self.update_status()

    def check_folder_match(self, path):
        """If the folder name matches, make it visible in the tree and expand its parents."""
        if len(self.matched_folders) >= MAX_FOLDER_MATCHES:
            return
        if not matches_keywords(os.path.basename(path), self.keywords):
            return

        # Skip if a parent already matched, its content is shown anyway
        parent = os.path.dirname(path)
        while len(parent) > len(self.root_path):
            if parent in self.matched_folders:
                return
            parent = os.path.dirname(parent)

        self.matched_folders.add(path)
        self.reveal_in_tree(path)

    def reveal_in_tree(self, path):
        """Makes Qt load the folder (and its parents) and expands the parents."""
        self.fs_model.index(path)
        parts = relative_parts(path, self.root_path) or []
        current = self.root_path
        for part in parts[:-1]:
            current = child_path(current, part)
            proxy_index = self.folder_proxy.mapFromSource(self.fs_model.index(current))
            if proxy_index.isValid():
                self.folder_tree.expand(proxy_index)

    # ---------------- Thumbnails ----------------

    def start_uncached_thumbnails(self):
        self.thumb_queue.extend(self.uncached_queue)
        self.uncached_queue = []
        if self.thumb_queue:
            self.thumb_timer.start()

    def process_thumbnail_queue(self):
        if not self.thumb_queue:
            self.thumb_timer.stop()
            return

        generation, path = self.thumb_queue.pop(0)
        image, width, height = load_thumbnail(path)
        self.on_thumbnail_ready(generation, path, image, width, height)

    def on_thumbnail_ready(self, generation, path, image, width, height):
        if generation != self.generation:
            return

        item = self.items_by_path.get(path)
        if item is None:
            return

        if image is None:
            item.setIcon(self.no_preview_icon)
            item.setToolTip(path + "\nPreview could not be loaded")
            return

        item.setIcon(QtGui.QIcon(QtGui.QPixmap.fromImage(image)))
        if width > 0 and height > 0:
            item.setToolTip("{}\n{} x {}".format(path, width, height))

    # ---------------- Slots ----------------

    def on_path_entered(self):
        self.set_path(self.path_field.text())

    def on_browse(self):
        start_dir = self.current_path if self.current_path else os.path.expanduser("~")
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select folder", start_dir)
        if folder:
            self.set_path(folder)

    def on_up(self):
        if not self.current_path:
            return
        parent = normalize_path(os.path.dirname(self.current_path))
        if not parent or parent == self.current_path:
            return
        if self.current_path == self.root_path:
            # Already at the top of the tree, move the root up one level
            self.set_path(parent)
        elif parent == self.root_path:
            self.folder_tree.clearSelection()
            self.folder_tree.setCurrentIndex(QtCore.QModelIndex())
            self.show_folder(parent)
        else:
            self.select_in_tree(parent)

    def on_tree_current_changed(self, current, previous):
        if not current.isValid():
            return
        path = normalize_path(self.fs_model.filePath(self.folder_proxy.mapToSource(current)))
        if path and path != self.current_path:
            self.show_folder(path)

    def on_image_double_clicked(self, item):
        self.add_to_image_manager([item.data(PATH_ROLE)])

    def on_image_context_menu(self, pos):
        item = self.image_list.itemAt(pos)
        if item is None:
            return

        if not item.isSelected():
            self.image_list.clearSelection()
            item.setSelected(True)

        paths = sorted([i.data(PATH_ROLE) for i in self.image_list.selectedItems()],
                       key=lambda p: p.lower())

        menu = QtWidgets.QMenu(self)
        if len(paths) == 1:
            add_action = menu.addAction("Add to Image Manager")
        else:
            add_action = menu.addAction("Add {} Images to Image Manager (as Group)".format(len(paths)))

        # PySide6 has exec(), PySide2 only exec_()
        menu_exec = getattr(menu, "exec", None) or menu.exec_
        chosen = menu_exec(self.image_list.viewport().mapToGlobal(pos))
        if chosen == add_action:
            self.add_to_image_manager(paths)

    # ---------------- Image Manager ----------------

    def add_to_image_manager(self, paths):
        """Loads images into the Image Manager. Multiple images go into a named group."""
        if mari.projects.current() is None:
            self.set_status("No project open: the Image Manager needs an open project", error=True)
            return

        group_name = ""
        if len(paths) > 1:
            group_name, ok = QtWidgets.QInputDialog.getText(
                self, "Image Group", "Group name:",
                QtWidgets.QLineEdit.EchoMode.Normal, os.path.basename(self.current_path))
            if not ok:
                self.set_status("Loading cancelled")
                return
            group_name = group_name.strip() or os.path.basename(self.current_path)

        images = []
        failed = []
        already = 0
        first_error = ""
        for path in paths:
            try:
                result, was_loaded = load_image_into_manager(path)
                already += int(was_loaded)
                if isinstance(result, (list, tuple)):
                    images.extend(result)
                elif result is not None:
                    images.append(result)
                else:
                    failed.append(os.path.basename(path))
                    first_error = first_error or "mari.images.open returned None"
            except Exception as exc:
                failed.append(os.path.basename(path))
                first_error = first_error or "{}: {}".format(type(exc).__name__, exc)
                log("Failed to load {}: {}".format(path, exc), error=True)

        if not images:
            self.set_status("Nothing loaded: " + first_error, error=True)
            return

        status = "{} image(s) loaded into the Image Manager".format(len(images) - already)
        if already:
            status += ", {} already loaded".format(already)

        if len(paths) > 1:
            if create_image_group(group_name, images):
                status += ", group '{}' created".format(group_name)
            else:
                status += ", group could not be created (see console)"

        if failed:
            status += ", {} failed".format(len(failed))

        self.set_status(status, error=bool(failed))

    # ---------------- Helpers ----------------

    def set_status(self, text, error=False):
        self.status_label.setStyleSheet("color: #e06c6c;" if error else "")
        self.status_label.setText(text)

    def key(self, name):
        """Settings key for this tab, e.g. tabs/0/last_root"""
        return "tabs/{}/{}".format(self.tab_id, name)

    def stop(self):
        """Stops all background work (used when the tab is closed or the palette is reloaded)."""
        for timer in (self.thumb_timer, self.delay_timer, self.index_timer, self.search_timer):
            timer.stop()
        self.thumb_queue = []
        self.uncached_queue = []


class TextureManagerWidget(QtWidgets.QWidget):
    """Tab container: one tab per library. Tabs are saved and restored on restart."""

    def __init__(self, parent=None):
        super(TextureManagerWidget, self).__init__(parent)
        self.settings = get_settings()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs)

        add_btn = QtWidgets.QToolButton()
        add_btn.setIcon(make_plus_icon(self.palette().color(QtGui.QPalette.ColorRole.ButtonText), 18))
        add_btn.setIconSize(QtCore.QSize(18, 18))
        add_btn.setToolTip("Add library tab")
        add_btn.setAutoRaise(True)
        add_btn.clicked.connect(self.on_add_tab)
        self.tabs.setCornerWidget(add_btn, QtCore.Qt.Corner.TopRightCorner)

        self.tabs.tabCloseRequested.connect(self.on_close_tab)
        self.tabs.tabBar().tabMoved.connect(lambda _from, _to: self.save_tab_order())
        self.tabs.tabBar().tabBarDoubleClicked.connect(self.on_rename_tab)

        # Restore tabs ("0" is also the tab that inherits settings from before tabs existed)
        tab_ids = [t.strip() for t in str(self.settings.value("tab_ids", "") or "").split(",")
                   if t.strip().isdigit()]
        for tab_id in tab_ids or ["0"]:
            self.add_tab(tab_id)
        current = to_int(self.settings.value("current_tab", 0))
        self.tabs.setCurrentIndex(max(0, min(current, self.tabs.count() - 1)))
        self.tabs.currentChanged.connect(lambda i: self.settings.setValue("current_tab", i))
        self.save_tab_order()

    def all_tabs(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def add_tab(self, tab_id=None):
        if tab_id is None:
            used = [to_int(t.tab_id) for t in self.all_tabs()]
            tab_id = str(max(used) + 1 if used else 0)
        tab = LibraryTab(tab_id)
        tab.root_changed_callback = self.update_tab_title
        self.tabs.addTab(tab, "")
        self.update_tab_title(tab)
        return tab

    def update_tab_title(self, tab):
        index = self.tabs.indexOf(tab)
        if index < 0:
            return
        custom_name = tab.settings.value(tab.key("name"), "")
        title = custom_name or (os.path.basename(tab.root_path) if tab.root_path else "New Library")
        self.tabs.setTabText(index, title)
        self.tabs.setTabToolTip(index, tab.root_path or "No path set")

    def save_tab_order(self):
        self.settings.setValue("tab_ids", ",".join(t.tab_id for t in self.all_tabs()))

    def on_add_tab(self):
        tab = self.add_tab()
        self.tabs.setCurrentWidget(tab)
        self.save_tab_order()
        tab.path_field.setFocus()

    def on_close_tab(self, index):
        tab = self.tabs.widget(index)
        if self.tabs.count() == 1:
            return  # always keep one tab
        tab.stop()
        self.settings.remove("tabs/{}".format(tab.tab_id))
        self.tabs.removeTab(index)
        tab.deleteLater()
        self.save_tab_order()

    def on_rename_tab(self, index):
        """Double-click a tab to give it a custom name (empty = use the folder name)."""
        if index < 0:
            return
        tab = self.tabs.widget(index)
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Rename Tab", "Tab name (empty = folder name):",
            QtWidgets.QLineEdit.EchoMode.Normal, self.tabs.tabText(index))
        if not ok:
            return
        name = name.strip()
        if name and name != os.path.basename(tab.root_path):
            tab.settings.setValue(tab.key("name"), name)
        else:
            tab.settings.remove(tab.key("name"))
        self.update_tab_title(tab)

    def stop(self):
        for tab in self.all_tabs():
            tab.stop()


def create_palette():
    """Creates the palette, removing an existing one first (safe to re-run)."""
    global _widget

    is_reload = _widget is not None
    if is_reload:
        try:
            _widget.stop()
        except Exception:
            pass

    try:
        if mari.palettes.find(PALETTE_NAME) is not None:
            mari.palettes.remove(PALETTE_NAME)
    except Exception:
        pass

    _widget = TextureManagerWidget()
    palette = mari.palettes.create(PALETTE_NAME, _widget)

    # On a manual re-run bring it to the front; on Mari startup only show it without stealing focus
    try:
        if is_reload:
            palette.showInFront()
        elif SHOW_ON_STARTUP:
            palette.show()
    except Exception:
        pass

    return palette


create_palette()