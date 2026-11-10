"""
Texture Manager for Mari
Dockable texture browser: folder tree, cached thumbnails, loading into the Image Manager.
Works with PySide6 (Mari 8) and PySide2 (Mari 7.x).
"""
import os
import traceback
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

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".tga", ".bmp",
                    ".exr", ".tx", ".hdr", ".psd")

PATH_ROLE = QtCore.Qt.ItemDataRole.UserRole

# Keep a global reference so the widget is not garbage collected (survives re-running in the console)
try:
    _widget
except NameError:
    _widget = None


def normalize_path(path):
    path = path.strip().strip('"').strip("'")
    if not path:
        return ""
    path = os.path.normpath(os.path.expanduser(path))
    return path.replace("\\", "/")


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
    return "{}/{}/{}.thumb{}.jpg".format(folder, CACHE_FOLDER_NAME, name, THUMB_SIZE)


def is_cache_valid(path):
    cache_path = get_cache_path(path)
    try:
        return os.path.isfile(cache_path) and os.path.getmtime(cache_path) >= os.path.getmtime(path)
    except OSError:
        return False


def save_thumbnail_cache(image, cache_path):
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        image.convertToFormat(QtGui.QImage.Format.Format_RGB888).save(cache_path, "JPG", 85)
    except Exception as exc:
        print("TEXTURE MANAGER: Could not write cache ({}): {}".format(cache_path, exc))


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
        print("TEXTURE MANAGER: Preview failed for {}: {}".format(path, reader.errorString()))
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


def load_image_into_manager(path):
    return mari.images.open(path)


def create_image_group(name, images):
    """addToImageGroup creates the group if it does not exist yet."""
    try:
        mari.images.addToImageGroup(list(images), name)
        return True
    except Exception as exc:
        traceback.print_exc()
        mari.app.log("TEXTURE MANAGER: addToImageGroup failed: {}".format(exc))
        return False


class FolderFilterProxy(QtCore.QSortFilterProxyModel):
    """Hides the cache folder and hidden folders in the folder tree."""

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        name = model.fileName(model.index(source_row, 0, source_parent))
        return name != CACHE_FOLDER_NAME and not name.startswith(".")


class TextureManagerWidget(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super(TextureManagerWidget, self).__init__(parent)

        self.current_path = ""
        self.root_path = ""
        self.settings = QtCore.QSettings("TextureManager", "MariTextureManager")

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
        main_layout.addWidget(self.splitter, 1)
        main_layout.addWidget(self.status_label, 0)

        # ---------------- Signals ----------------
        self.path_field.returnPressed.connect(self.on_path_entered)
        self.browse_btn.clicked.connect(self.on_browse)
        self.up_btn.clicked.connect(self.on_up)
        self.folder_tree.selectionModel().currentChanged.connect(self.on_tree_current_changed)
        self.image_list.customContextMenuRequested.connect(self.on_image_context_menu)

        # ---------------- Restore last location ----------------
        last_root = self.settings.value("last_root", "")
        last_path = self.settings.value("last_path", "")
        if last_root and os.path.isdir(last_root):
            self.set_path(last_root)
            if last_path and last_path != last_root and os.path.isdir(last_path) \
                    and normalize_path(last_path).startswith(self.root_path + "/"):
                self.select_in_tree(normalize_path(last_path))
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
        self.settings.setValue("last_root", path)
        source_index = self.fs_model.setRootPath(path)
        self.folder_tree.setRootIndex(self.folder_proxy.mapFromSource(source_index))
        self.folder_tree.clearSelection()

    def show_folder(self, path):
        """Shows a folder's images in the grid, the tree stays as it is."""
        self.current_path = path
        self.path_field.setText(path)
        self.settings.setValue("last_path", path)

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
            path = self.current_path + "/" + name
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
        self.set_status(status)

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
        first_error = ""
        for path in paths:
            try:
                result = load_image_into_manager(path)
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
                traceback.print_exc()
                mari.app.log("TEXTURE MANAGER: Failed to load {}: {}".format(path, exc))

        if not images:
            self.set_status("Nothing loaded: " + first_error, error=True)
            return

        status = "{} image(s) loaded into the Image Manager".format(len(images))

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


def create_palette():
    """Creates the palette, removing an existing one first (safe to re-run)."""
    global _widget

    if _widget is not None:
        try:
            _widget.thumb_timer.stop()
            _widget.delay_timer.stop()
            _widget.thumb_queue = []
            _widget.uncached_queue = []
        except Exception:
            pass

    try:
        if mari.palettes.find(PALETTE_NAME) is not None:
            mari.palettes.remove(PALETTE_NAME)
    except Exception:
        pass

    _widget = TextureManagerWidget()
    palette = mari.palettes.create(PALETTE_NAME, _widget)

    try:
        palette.showInFront()
    except Exception:
        palette.show()

    return palette


create_palette()