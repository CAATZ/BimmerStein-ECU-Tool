"""MS42/MS43 stock configuration in the existing file/ECU editor."""

from pathlib import Path

from PyQt5.QtWidgets import (
    QAbstractItemView, QGroupBox, QHeaderView, QLabel,
    QMessageBox, QFileDialog, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

import ms42_ms43_image as images
from gui_controls import ConfigComboBox


class MS42MS43ConfigDesktop:
    def _ms42_ms43_build_config(self, layout, introduction, footnote):
        self._ms42_ms43_config_file_identity = None
        self._ms42_ms43_config_combos = {}
        self._ms42_ms43_config_key = None
        self._ms42_ms43_config_notes = (introduction, footnote)
        self._ms41_config_tooltips = {button: button.toolTip() for button in (
            self.btn_config_read_ecu, self.btn_config_copy_ecu, self.btn_config_write_ecu)}
        self._ms42_ms43_config_group = QGroupBox("ECU settings")
        body = QVBoxLayout(self._ms42_ms43_config_group)
        self._ms42_ms43_config_table = QTableWidget(0, 2)
        table = self._ms42_ms43_config_table
        table.setHorizontalHeaderLabels(["Setting", "Value"])
        table.verticalHeader().hide()
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        body.addWidget(table)
        self._ms42_ms43_config_detected = QLabel("Read from ECU to show reported configuration.")
        self._ms42_ms43_config_detected.setWordWrap(True)
        body.addWidget(self._ms42_ms43_config_detected)
        layout.addWidget(self._ms42_ms43_config_group)
        self._ms42_ms43_config_group.hide()

    def _ms42_ms43_config_context(self):
        if self._config_data is not None:
            return self._ms42_ms43_config_file_identity
        if self._is_ms42_ms43():
            return {"family": self._ds2.family, "software_id": self._ecu_cal_id}
        return None

    def _ms42_ms43_update_config(self):
        if not hasattr(self, "_ms42_ms43_config_group"):
            return False
        from ms42_ms43_config import FULL_IMAGE_REQUIRED
        from ms42_ms43_config_patches import PatchSetting, features_for

        context = self._ms42_ms43_config_context()
        active = context is not None
        self._ms42_ms43_config_group.setVisible(active)
        layout = self._ms42_ms43_config_group.parentWidget().layout()
        layout.setStretch(layout.indexOf(self._ms42_ms43_config_group), int(active))
        layout.setStretch(layout.count() - 1, int(not active))
        for widget in (*self._config_section_groups.values(), *self._ms42_ms43_config_notes):
            widget.setVisible(not active)
        if not active:
            for button, tooltip in self._ms41_config_tooltips.items():
                button.setToolTip(tooltip)
            return False
        family, software = context["family"], context["software_id"]
        key = (family, software, self._config_path,
               None if self._config_data is not None else id(self._ds2))
        features = features_for(family, software)
        if key != self._ms42_ms43_config_key:
            self._ms42_ms43_config_key = key
            self._ms42_ms43_config_combos = {}
            self._ms42_ms43_config_original_values = {}
            table = self._ms42_ms43_config_table
            table.setRowCount(len(features))
            for row, feature in enumerate(features):
                item = QTableWidgetItem(feature.name)
                item.setToolTip(feature.note)
                table.setItem(row, 0, item)
                combo = ConfigComboBox()
                if feature.full_file_only and (
                        (self._config_data is None and not isinstance(feature, PatchSetting)) or
                        (self._config_data is not None and len(self._config_data) != images.FULL_SIZE)):
                    combo.addItem(FULL_IMAGE_REQUIRED)
                else:
                    combo.addItem("Read current value…")
                    combo.addItems([label for label, _value in feature.options])
                combo.setToolTip(feature.note)
                combo.setEnabled(False)
                table.setCellWidget(row, 1, combo)
                self._ms42_ms43_config_combos[feature.name] = combo
            table.resizeRowsToContents()
            row_height = max(table.fontMetrics().lineSpacing() + 14, 28)
            table.setMinimumHeight(table.horizontalHeader().height() + row_height * min(len(features), 4) + 4)
            self._ms42_ms43_config_detected.setText(
                "Read from ECU to show reported configuration." if features else
                f"Stock settings are not mapped for {family} software {software}.")
        idle = not self._task_busy and not self._actuator_cleanup_pending
        loaded = self._config_data is not None
        mode = "File mode — close file for ECU operations" if loaded else "ECU settings"
        self._ms42_ms43_config_group.setTitle(f"{family} {software} — {mode}")
        connected = self._is_ms42_ms43() and not self._ms42_ms43_link_unavailable()
        same_family = connected and self._ds2.family == family
        self.btn_config_load.setEnabled(idle)
        self.btn_config_close.setEnabled(idle and loaded)
        self.btn_config_save.setEnabled(idle and loaded and bool(features))
        self.btn_config_copy_ecu.setEnabled(idle and loaded and same_family and bool(features))
        self.btn_config_read_ecu.setEnabled(idle and not loaded and connected and bool(features))
        self.btn_config_get_file.setEnabled(idle and not loaded and connected and bool(features))
        self.btn_config_write_ecu.setEnabled(
            idle and not loaded and connected and bool(self._ms42_ms43_config_changes()))
        for button in (self.btn_config_read_ecu, self.btn_config_copy_ecu):
            button.setToolTip("Read calibration settings, supported program options and ECU-reported configuration.")
        if loaded:
            self.btn_config_read_ecu.setToolTip(
                "Close the loaded file to read directly from the ECU. "
                "To copy ECU settings into this file, use Copy flags from ECU.")
        self.btn_config_write_ecu.setToolTip(
            "Apply settings with the existing writer. Program options require program and calibration writing.")
        return True

    def _ms42_ms43_config_values(self, values, *, remember=True):
        from ms42_ms43_config import FULL_IMAGE_REQUIRED
        from ms42_ms43_config_patches import PatchSetting, features_for

        context = self._ms42_ms43_config_context()
        if context is None:
            return
        features = {feature.name: feature for feature in features_for(context["family"], context["software_id"])}
        for name, value in values.items():
            combo = self._ms42_ms43_config_combos.get(name)
            if combo is None:
                continue
            feature = features[name]
            if value == FULL_IMAGE_REQUIRED or (feature.full_file_only and (
                    (self._config_data is None and not isinstance(feature, PatchSetting)) or
                    (self._config_data is not None and len(self._config_data) != images.FULL_SIZE))):
                continue
            if combo.findText(value) < 0:
                combo.addItem(value)
            combo.setCurrentText(value)
            combo.setEnabled(not (isinstance(feature, PatchSetting) and value == "Custom / unsupported bytes"))
            if remember:
                self._ms42_ms43_config_original_values[name] = value
        self._update_config_buttons()

    def _ms42_ms43_config_changes(self):
        return {name: combo.currentText() for name, combo in self._ms42_ms43_config_combos.items()
                if combo.isEnabled() and combo.currentText() != "Read current value…"}

    def _ms42_ms43_config_load(self, path, data):
        from ms42_ms43_config_patches import read_config

        identity = images.image_identity(data)
        values = read_config(data, identity["family"], identity["software_id"])
        self._config_data, self._config_path = bytes(data), str(path)
        self._ms42_ms43_config_file_identity = identity
        self._ms42_ms43_config_key = None
        self._reset_live_config_state()
        self._update_config_buttons()
        self._ms42_ms43_config_values(values)
        self.lbl_config_file.setText(f"{Path(path).name} ({identity['family']} {identity['software_id']})")
        self._log(f"Configuration loaded: {Path(path).name}", "ok")

    def _ms42_ms43_config_read(self, copy=False):
        from ms42_ms43_config_patches import read_live_config
        from ms42_ms43_diagnostics import read_learned_config

        def task(log_fn, progress_fn):
            values = read_live_config(self._ds2, self._ecu_cal_id)
            try:
                learned = read_learned_config(self._ds2)
            except Exception as error:
                log_fn(f"Reported configuration unavailable: {error}", "warn")
                learned = {}
            return values, learned

        def done(result):
            values, learned = result
            self._ms42_ms43_config_values(values)
            self._ms42_ms43_config_detected.setText(
                "Reported configuration: " + "; ".join(
                    f"{key}: {'Yes' if value is True else 'No' if value is False else value}"
                    for key, value in learned.items())
                if learned else "Reported configuration unavailable.")
            self._log("Configuration copied from ECU." if copy else "Configuration read.", "ok")

        self._run_task(task, on_success=done)

    def _ms42_ms43_config_get_file(self):
        from ms42_ms43_config_patches import read_config

        path, _ = QFileDialog.getOpenFileName(self, "Get settings from image", "", "Binary Files (*.bin)")
        if not path:
            return
        try:
            data = Path(path).read_bytes()
            identity = images.image_identity(data)
            if identity["family"] != self._ds2.family:
                raise ValueError("The configuration image belongs to another ECU family.")
            self._ms42_ms43_config_values(
                read_config(data, identity["family"], identity["software_id"]), remember=False)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Configuration Unavailable", str(error))

    def _ms42_ms43_config_save(self):
        from ms42_ms43_config_patches import apply_config

        identity = self._ms42_ms43_config_file_identity
        try:
            data = apply_config(self._config_data, self._ms42_ms43_config_changes(),
                                identity["family"], identity["software_id"])
            if self.chk_config_fix.isChecked():
                context = None
                if identity["family"] == "MS43" and identity["partial"]:
                    context = self._backup_mgr.checksum_context(self._config_data)
                    if context is None:
                        full_path, _ = QFileDialog.getOpenFileName(
                            self, "Matching MS43 full image for checksum context", "", "Binary Files (*.bin)")
                        if not full_path:
                            return
                        context = Path(full_path).read_bytes()
                        full_identity = images.image_identity(context, "MS43")
                        if full_identity["partial"] or full_identity["software_id"] != identity["software_id"]:
                            raise ValueError("Checksum context must be a matching MS43 full image.")
                data, _details = images.correct_checksums(data, identity["family"], context=context)
            path, _ = QFileDialog.getSaveFileName(
                self, "Save configured image", str(Path(self._config_path).with_stem(
                    Path(self._config_path).stem + "_config")), "Binary Files (*.bin)")
            if path:
                Path(path).write_bytes(data)
                self._log(f"Configured image saved: {Path(path).name}", "ok")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Configuration Not Saved", str(error))

    def _ms42_ms43_config_write(self):
        from ms42_ms43_config_patches import apply_config, patches_for, requires_full_write

        changes = self._ms42_ms43_config_changes()
        if not changes:
            return
        family, software = self._ds2.family, self._ecu_cal_id
        original = self._ms42_ms43_config_original_values
        patch_names = {patch.name for patch in patches_for(family, software)}
        changes = {name: value for name, value in changes.items()
                   if name not in patch_names or value != original.get(name)}
        mode = "full" if requires_full_write(changes, original, family, software) else "tune"
        force_slow = self.chk_force_slow_ds2.isChecked()

        def task(log_fn, progress_fn):
            with self._ds2.read_transfer(force_slow=force_slow,
                                         log_fn=log_fn, use_agent=True) as reader:
                read = reader.read_full if mode == "full" else reader.read_tune
                current = read(progress_cb=progress_fn, log_fn=log_fn)
            return apply_config(current, changes, family, software)

        self._run_task(task, on_success=lambda data: self._ms42_ms43_write(
            data, "ECU configuration", mode))
