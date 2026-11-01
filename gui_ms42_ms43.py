"""MS42/MS43 operations using the desktop's existing controls and task runner."""

from pathlib import Path
from html import escape

from PyQt5.QtWidgets import (
    QCheckBox, QFileDialog, QInputDialog, QMessageBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QScrollArea, QVBoxLayout, QWidget,
)

import ms42_ms43_image as images
from ms42_ms43_resident import SUPPORTED_SOFTWARE
from gui_ms42_ms43_config import MS42MS43ConfigDesktop
from gui_ms42_ms43_storage import MS42MS43StorageDesktop


class MS42MS43Desktop(MS42MS43StorageDesktop, MS42MS43ConfigDesktop):
    def _ms42_ms43_show_transfer_rate(self, baud, source):
        if baud:
            self.lbl_observed_rate.setText(f"Current link: {baud:,} baud ({source})")
        else:
            state = "changing rate…" if source == "transition" else "unconfirmed"
            self.lbl_observed_rate.setText(f"Current link: {state}")
        if self._task_busy:
            self.lbl_high_speed_mode.setText(
                f"Link: {baud:,} baud" if baud else f"Link: {state}")

    def _ms42_ms43_extract(self, data, source):
        try:
            partial = images.extract_calibration(data)
            path, _ = QFileDialog.getSaveFileName(
                self, f"Save {len(partial) // 1024} KB Partial",
                str(Path(source).with_stem(Path(source).stem + "_partial")),
                "Binary Files (*.bin);;All Files (*)")
            if path:
                Path(path).write_bytes(partial)
                self._log(f"Saved {len(partial) // 1024} KB calibration: {Path(path).name}", "ok")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Extraction Failed", str(error))

    def _ms42_ms43_merge(self, full, source):
        profile = images.image_profile(full)
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select {profile.name} {profile.partial_size // 1024} KB Partial",
            "", "Binary Files (*.bin);;All Files (*)")
        if not path:
            return
        try:
            merged, details = images.merge_calibration(
                full, Path(path).read_bytes(), correct_checksum=self.chk_merge_fix.isChecked())
            output, _ = QFileDialog.getSaveFileName(
                self, "Save Merged Full ROM",
                str(Path(source).with_stem(Path(source).stem + "_merged")),
                "Binary Files (*.bin);;All Files (*)")
            if output:
                Path(output).write_bytes(merged)
                for detail in details:
                    self._log(detail)
                self._log(f"Saved {profile.name} merged image: {Path(output).name}", "ok")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Merge Failed", str(error))

    def _restore_ms41_labels(self):
        self._ms42_ms43_data_view = False
        if getattr(self, "_ms42_ms43_vin_view", False):
            self._clear_identity_tab_state()
        self._ms42_ms43_vin_view = False
        self._ms42_ms43_vin_info = None
        self.identity_data_group.setTitle("BOOT Identity Data")
        self.identity_strings_label.setText("Boot strings:")
        self._id_field_names["isn"].show()
        self._id_labels["isn"].show()
        self._id_labels["isn"].setToolTip("")
        self._id_labels["part"].setToolTip("")
        self._info_labels["EWS2 ISN"].setToolTip("")
        self._info_labels["BMW Program Part Number"].setToolTip("")
        self.btn_id_read_flash_ecu.setText("Read BOOT Identity")
        self.btn_id_read_flash_ecu.setToolTip(
            "Requires Soft-BSL. Reads 16 KB on BOTTOM or the complete fused 64 KB SA7 sector "
            "on 29F400 TOP, with automatic baud fallback. A full 256 KB backup can be created "
            "first and reused without a second ECU read.")
        self.id_vin_current.setPlaceholderText("Read BOOT Identity first")
        if hasattr(self, "_ms41_adaptation_groups"):
            for group in self._ms41_adaptation_groups:
                group.show()
            self._ms42_ms43_adapt_values.hide()
        if hasattr(self, "_ms41_boot_identity_choice"):
            self.chk_boot_preserve_identity.setChecked(self._ms41_boot_identity_choice)
            del self._ms41_boot_identity_choice
        self.chk_boot_preserve_identity.setText("Preserve ECU identity during boot writes")
        for key, label in self._info_field_names.items():
            label.setText(f"{key}:")
        for field in ("Current AIF Record", "First AIF Record"):
            self._info_field_names[field].show()
            self._info_labels[field].show()
            self._info_labels[field].setToolTip("")
        for field in ("ECU Number", "ISN Data", "Reported BMW Part Number"):
            self._info_field_names[field].hide()
            self._info_labels[field].hide()
        self._info_supply_group.hide()
        for field in ("Battery Voltage", "Ignition Supply Voltage"):
            self._info_field_names[field].hide()
            self._info_labels[field].hide()
        for button, text in (
                (self.btn_read_full, "📥  Read Full ROM  (256 KB)"),
                (self.btn_read_tune, "📥  Read Tune Region  (24 KB)"),
                (self.btn_write_full, "📤  Write Full ROM  (256 KB)"),
                (self.btn_write_tune, "📤  Write Tune Region  (24 KB)")):
            button.setText(text)
            button.setToolTip("")
        self.btn_id_read_ecu.setText("Read ISN")
        self.btn_id_read_ecu.setToolTip(
            "Read only the authoritative four-digit DME ISN. This fresh live value is the "
            "only source allowed to arm Send to EWS.")
        self.btn_ews_send.setText("Send to EWS (0x44)…")
        self.btn_ews_send.setToolTip("")
        self.ews_group.setTitle("EWS2 Alignment  (write the DME's 4-digit ISN to the immobilizer, module 0x44)")
        self.id_ews_isn.show()
        self.ews_isn_label.show()
        self.btn_virginize.hide()
        self.btn_virginize.setEnabled(False)

    def _is_ms42_ms43(self):
        return getattr(getattr(self, "_ds2", None), "family", None) in images.PROFILES

    def _diagnostic_link(self):
        return self._ds2.transport if self._is_ms42_ms43() else self._ds2

    def _ms42_ms43_link_unavailable(self):
        return self._is_ms42_ms43() and (
            self._ms42_ms43_interrupted() or getattr(self._ds2, "baud_uncertain", False))

    def _ms42_ms43_connected(self, info):
        self._reset_live_config_state()
        self._clear_identity_tab_state()
        self._ecu_variant = self._ecu_program_variant = info["family"]
        self._ecu_id = info["hardware_id"]
        self._ecu_cal_id = info["software_id"]
        if self._ms42_ms43_storage_snapshot is None:
            self._ms42_ms43_select_storage_layout(
                (info["family"], "retained" if info["family"] == "MS42" else "eeprom"))
        self._ecu_vin = info.get("vin", "")
        self._ecu_identity_source = None
        self._ecu_softbsl_marker = None
        self._ecu_softbsl_hook_present = False
        self._ecu_calguard_recovery_ready = False
        self._ecu_chip_sig = b""
        for group in self._ms41_adaptation_groups:
            group.hide()
        self._ms42_ms43_adapt_values.setRowCount(0)
        self._ms42_ms43_adapt_values.show()
        self._last_ident_raw = info.get("raw_ident", b"")
        self._post_write_cycle_pending = False
        self.chk_bootloader_write.setChecked(False)
        self.id_ews_frames.setPlainText("Choose Synchronize EWS or Reset Start Value.")
        if self.chk_force_softbsl.isChecked() or self.rb_recovery_boot.isChecked():
            self.rb_recovery_auto.setChecked(True)
        self._d2xx_checked = True
        self._d2xx_ok = bool(self._ds2.uses_d2xx)
        self._update_d2xx_warning()
        self._ms42_ms43_show_info(info)
        self._flash_chip_note.setText("Bootloader preserved")
        self.lbl_status.setText(f"● Connected ({info['family']})")
        self.lbl_status.setStyleSheet("color:#5f5; font-weight:bold;")
        self.btn_connect.setText("Disconnect")
        self._reset_diag_scan(True)
        self._ms42_ms43_controls()
        self._update_transfer_mode()
        self._show_transfer_rate(self._ds2.baud, "DS2 connection")
        self._log(f"Connected to {info['family']} · software {info['software_id']}", "ok")

    def _ms42_ms43_show_info(self, info):
        self._info_field_names["Reported ECU Identifier"].setText("Hardware ID:")
        self._info_field_names["BMW Program Part Number"].setText("DATEN Program Number:")
        self._info_field_names["Calibration ID"].setText("Software ID:")
        self._info_field_names["EWS2 ISN"].setText("ISN:")
        for field in ("ECU Number", "ISN Data", "Reported BMW Part Number"):
            self._info_field_names[field].show()
            self._info_labels[field].show()
        for label in self._info_labels.values():
            label.setText("—")
            label.setToolTip("")
        for key, value in {
            "ECU Family": info["family"],
            "Reported ECU Identifier": info.get("hardware_id") or self._ecu_id or "Unavailable",
            "Calibration ID": info.get("software_id") or self._ecu_cal_id or "Unavailable",
            "VIN": info.get("vin") or "Unavailable",
        }.items():
            self._info_labels[key].setText(value)
        fields = {
            "ECU Number": "ecu_number", "EWS2 ISN": "isn", "ISN Data": "isn_status",
            "Transmission Mode": "transmission_mode",
            "Program / Calibration Match": "program_calibration_match",
            "Flash Command-Set Driver": "flash_driver",
            "BMW Program Part Number": "program_part_number",
            "Reported BMW Part Number": "reported_identifier",
            "BMW Hardware Number": "bmw_hardware_number", "Coding Index": "coding_index",
            "Diagnostic Index": "diagnostic_index", "Bus Index": "bus_index",
            "Software Index": "software_index", "Change Index": "change_index",
            "Supplier Number": "supplier_number", "DME Production Serial": "dme_production_serial",
            "Recorded ZB / ZUSB": "recorded_zb_zusb", "AIF Record Date": "programming_date",
            "Recorded Software Number": "recorded_software_number",
            "Recording Authority Number": "recorded_authority_number",
            "Programming Count": "programming_count", "BMW DATEN Lineage": "daten_lineage",
        }
        for label, key in fields.items():
            if info.get(key) is not None:
                self._info_labels[label].setText(escape(str(info[key])))
        if len(info.get("program_candidates", ())) > 1:
            self._info_labels["BMW Program Part Number"].setToolTip(
                "Program reference matches multiple DATEN numbers: " + ", ".join(info["program_candidates"]))
        if info.get("manufacturing_week") is not None:
            self._info_labels["Manufacturing Week / Year"].setText(
                f"{info['manufacturing_week']} / {info.get('manufacturing_year', '—')}")
        records = info.get("aif_records", ())
        for widget in (self._info_field_names["First AIF Record"], self._info_labels["First AIF Record"]):
            widget.setVisible(len(records) > 1)
        if records:
            from ms42_ms43_diagnostics import format_aif_record
            self._info_labels["Current AIF Record"].setText(str(records[-1]["aif_record_number"]))
            self._info_labels["Current AIF Record"].setToolTip(format_aif_record(records[-1]))
            first = records[0]
            self._info_labels["First AIF Record"].setText(
                escape(f"{first['aif_record_number']} · {first.get('programming_date') or 'Date unavailable'}"))
            self._info_labels["First AIF Record"].setToolTip(format_aif_record(first))
        supply = info.get("supply_voltages", {})
        self._info_supply_group.setVisible(bool(supply))
        for field, key in (("Battery Voltage", "battery"), ("Ignition Supply Voltage", "ignition")):
            self._info_field_names[field].setVisible(key in supply)
            self._info_labels[field].setVisible(key in supply)
            if key in supply:
                self._info_labels[field].setText(f"{supply[key]:.2f} V")
        self._info_field_names["Battery Voltage"].setText(
            "Battery Voltage (after main relay):" if info["family"] == "MS43" else "Battery Voltage:")
        self._info_field_names["Ignition Supply Voltage"].setText("Ignition Supply Voltage (KL15):")
        self._last_ident_raw = info.get("raw_ident", b"")
        self.raw_ident_view.setPlainText(self._last_ident_raw.hex(" ").upper())
        self._id_labels["part"].setText(info.get("reported_identifier") or "—")
        self._id_labels["part"].setToolTip("Part number reported by the ECU identification response.")
        self._id_labels["serial"].setText(info.get("dme_production_serial") or "—")
        self._id_labels["isn"].setText(info.get("isn") or info.get("isn_status") or "Not read")
        self._id_labels["isn"].setToolTip(
            "Read ECU Info or Read VIN / AIF to refresh the stored DME ISN (hexadecimal). "
            "This does not confirm synchronization with the EWS module.")
        self._info_labels["EWS2 ISN"].setToolTip(self._id_labels["isn"].toolTip())
        self._ms42_ms43_identity_info = info

    def _ms42_ms43_controls(self):
        idle = (not self._task_busy and not self._ms42_ms43_link_unavailable()
                and not self._actuator_cleanup_pending)
        for button in (self.btn_info, self.btn_read_full, self.btn_read_tune,
                       self.btn_write_full, self.btn_write_tune,
                       self.btn_reset_adapt, self.btn_id_read_ecu, self.btn_ews_send,
                       self.btn_id_read_flash_ecu):
            button.setEnabled(idle)
        self.btn_softbsl_install.setEnabled(False)
        self.btn_read_adaptations.setEnabled(idle)
        self._refresh_live_channels()
        self._set_live_buttons_enabled(idle)
        self._refresh_actuator_tests()
        self._set_roughness_buttons_enabled()
        self._update_config_buttons()
        self._update_eeprom_buttons()
        self._set_backup_buttons_enabled(idle)
        self._update_diag_actions()
        self._update_coding_actions()
        if not idle:
            for button in (self.btn_scan_modules, self.btn_read_dtc, self.btn_clear_dtc,
                           self.btn_read_module_coding, self.btn_write_module_coding,
                           self.btn_transmission_swap_check, self.btn_transmission_swap_convert,
                           self.btn_transmission_swap_verify):
                button.setEnabled(False)
        size = self._ds2.profile.partial_size // 1024
        self.btn_read_full.setText("📥  Read Full ROM  (512 KB)")
        self.btn_read_tune.setText(f"📥  Read Tune Region  ({size} KB)")
        self.btn_write_full.setText("📤  Write Full ROM  (512 KB)")
        self.btn_write_tune.setText(f"📤  Write Tune Region  ({size} KB)")
        self.btn_read_tune.setToolTip(f"Read the {size} KB calibration and save it to Bins.")
        self.btn_read_full.setToolTip(
            "Read all 512 KB through the RAM loader when supported. Otherwise read a "
            "diagnostic image with protected bytes masked. The saved backup records the read source.")
        self.btn_write_tune.setToolTip(f"Write {size} KB calibration from a partial or full file.")
        self.btn_write_full.setToolTip(
            "Write program and calibration. Boot code is preserved unless Include boot code is selected.")
        self.btn_id_read_ecu.setText("Synchronize EWS…")
        self.btn_id_read_ecu.setToolTip("Synchronize the DME and EWS3.3 using the shared link.")
        self.btn_ews_send.setText("Reset Start Value…")
        self.btn_ews_send.setToolTip("Reset the EWS3.3 start value using the stock handshake.")
        self.ews_group.setTitle("EWS3.3 Synchronization")
        self.id_ews_isn.hide()
        self.ews_isn_label.hide()
        self.btn_virginize.show()
        self.identity_data_group.setTitle("VIN and AIF History")
        self._ms42_ms43_vin_view = True
        self.identity_strings_label.setText("Programming history:")
        self._id_field_names["isn"].show()
        self._id_labels["isn"].show()
        self.btn_id_read_flash_ecu.setText("Read VIN / AIF")
        self.btn_id_read_flash_ecu.setToolTip(
            "Read the recorded VIN and BMW programming history, and refresh the reported "
            "part number, DME serial and ISN through stock diagnostics.")
        self.id_vin_current.setPlaceholderText("Read VIN / AIF first")
        if getattr(self, "_ms42_ms43_vin_info", None) is None:
            state = getattr(self._ds2, "write_state", None)
            unverified = (getattr(state, "operation", None) in ("vin", "virginize")
                          and getattr(state, "destructive_started", False))
            self.lbl_identity_cache.setText("VIN / AIF unverified" if unverified else "VIN / AIF not read")
        self._ms42_ms43_update_vin()
        for button in (self.chk_force_softbsl, self.rb_recovery_boot):
            button.setEnabled(False)
        self._ms42_ms43_boot_controls()

    def _ms42_ms43_boot_controls(self):
        link = getattr(self._ds2, "transport", None)
        enabled = (not self._task_busy and not self._ms42_ms43_link_unavailable()
                   and not self.chk_force_slow_ds2.isChecked()
                   and hasattr(getattr(link, "_ser", None), "parity")
                   and self._ecu_cal_id in SUPPORTED_SOFTWARE[self._ds2.family])
        self.chk_bootloader_write.setText("Include boot code (Full ROM)")
        self.chk_bootloader_write.setToolTip(
            "Update boot code using the custom RAM agent. Live ECU identity is preserved when selected. "
            "Boot readback is always verified before restart. An interrupted boot update "
            "can require hardware recovery.")
        self.chk_bootloader_write.setEnabled(enabled)
        if not enabled and not self._task_busy:
            self.chk_bootloader_write.setChecked(False)
        if not hasattr(self, "_ms41_boot_identity_choice"):
            self._ms41_boot_identity_choice = self.chk_boot_preserve_identity.isChecked()
            self.chk_boot_preserve_identity.setChecked(True)
        self.chk_boot_preserve_identity.setText("Preserve ECU identity during boot writes")
        self.chk_boot_preserve_identity.setEnabled(enabled and self.chk_bootloader_write.isChecked())
        self.chk_boot_preserve_identity.setToolTip(
            "Keep the live VIN/AIF history, ISN/pairing records and ECU identifiers when replacing boot code. "
            "Uncheck to use the selected file's identity. Separate retained-data sectors 0x4000–0x7FFF are always preserved.")
        self._flash_chip_note.setText(
            "Boot code included in full writes" if self.chk_bootloader_write.isChecked() else "Bootloader preserved")

    def _ms42_ms43_info(self):
        from ms42_ms43_diagnostics import read_identity

        def task(log_fn, progress_fn):
            info = read_identity(self._ds2)
            for warning in info.get("warnings", ()):
                log_fn(warning, "warn")
            try:
                info["supply_voltages"] = self._ds2.read_supply_voltages()
                label = "Battery voltage (after main relay)" if self._ds2.family == "MS43" else "Battery voltage"
                log_fn(f"{label}: {info['supply_voltages']['battery']:.2f} V")
                if "ignition" in info["supply_voltages"]:
                    log_fn(f"Ignition supply voltage (KL15): {info['supply_voltages']['ignition']:.2f} V")
            except Exception as error:
                log_fn(f"Supply voltages unavailable: {error}", "warn")
            return info
        self._run_task(task, on_success=self._ms42_ms43_show_info)

    def _ms42_ms43_interrupted(self):
        state = getattr(getattr(self, "_ds2", None), "write_state", None)
        return bool(state and state.destructive_started and not state.completed)

    def _ms42_ms43_confirm_disconnect(self):
        if not self._ms42_ms43_interrupted():
            return True
        retry_label = self._ms42_ms43_retry_label()
        storage = getattr(self._ds2.write_state, "operation", None) in ("eeprom_restore", "retained_restore")
        return QMessageBox.question(
            self, "Programming Incomplete",
            "The interrupted write still owns this connection. Keep ignition ON.\n\n"
            f"Disconnecting abandons the retained {retry_label} session; recovery "
            + ("will require reconnecting and loading the saved storage backup."
               if storage else "may then require reconnecting through the bootloader.")
            + "\n\nDisconnect anyway?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def _ms42_ms43_retry_label(self):
        operation = getattr(getattr(self._ds2, "write_state", None), "operation", None)
        return {"vin": "Retry VIN Write", "virginize": "Retry Virginize",
                "eeprom_restore": "Retry Storage Restore", "retained_restore": "Retry Storage Restore"}.get(operation, "Retry Same Image")

    def _ms42_ms43_retry(self):
        if not self._ms42_ms43_interrupted():
            return
        operation = getattr(self._ds2.write_state, "operation", None)
        identity_write = operation in ("vin", "virginize")
        storage_write = operation in ("eeprom_restore", "retained_restore")
        title = self._ms42_ms43_retry_label()
        message = (
            f"Complete the retained {'virginizing operation' if operation == 'virginize' else 'VIN write'} "
            "using its original saved data?\n\n"
            "Keep the same ECU powered and connected."
            if identity_write else
            "Complete the storage restore using its original saved target?\n\n"
            "Actual storage will be read before any remaining writes. Keep the same ECU powered and connected."
            if storage_write else
            "Restart programming with the retained image?\n\n"
            "Keep the same ECU powered and connected. This will erase and rewrite "
            "the selected regions, including boot code if selected originally.")
        if QMessageBox.question(
                self, title, message, QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        ecu = self._ds2
        target_identity = (images.image_identity(ecu._pending_image, ecu.family)
                           if operation == "full" else None)

        def task(log_fn, progress_fn):
            with ecu.transfer():
                return ecu.retry_write(progress_cb=progress_fn, log_fn=log_fn)

        def flash_done(_result):
            if target_identity is not None:
                self._ms42_ms43_written_revision(ecu, target_identity)
            self._show_flash_complete(
                "Recovery Complete", "Programming completed. Reconnect after the ignition cycle.")

        self._run_task(task, on_success=(self._ms42_ms43_storage_restored if storage_write else
            self._ms42_ms43_virginized if operation == "virginize" else
            self._ms42_ms43_vin_written if identity_write else
            flash_done),
            on_failure=self._ms42_ms43_storage_failed if storage_write else
            (lambda error: self._ms42_ms43_vin_failed(
                error, virginizing=operation == "virginize")) if identity_write else
            (lambda error: self._ms42_ms43_full_write_failed(error, ecu, target_identity))
            if target_identity is not None else None)

    def _ms42_ms43_update_vin(self):
        from ms42_ms43_vin import encode_vin, VIN_WRITE_SOFTWARE, PAIRING_STATUS

        enabled = ready = False
        candidate = self.id_vin_custom.text().strip()
        message, colour = "Enter the new 17-character VIN; Read VIN / AIF shows the current history.", "#888"
        link = getattr(self._ds2, "transport", None)
        if self._task_busy:
            message = "Another ECU operation is in progress."
        elif self._ms42_ms43_link_unavailable():
            message = "Complete the retained write or restore the connection before editing VIN."
        elif self._actuator_cleanup_pending:
            message = "Stop the active actuator test before editing VIN."
        elif (self.chk_force_slow_ds2.isChecked()
              or not hasattr(getattr(link, "_ser", None), "parity")
              or self._ecu_cal_id not in VIN_WRITE_SOFTWARE[self._ds2.family]):
            message = "VIN writing requires a verified identity layout and an Automatic RAM-agent connection."
            colour = "#e8c46a"
        else:
            ready = True
            if candidate:
                try:
                    encode_vin(candidate)
                except ValueError as error:
                    message, colour = str(error), "#e8c46a"
                else:
                    candidate = candidate.upper()
                    info = getattr(self, "_ms42_ms43_vin_info", None) or {}
                    if (candidate == self.id_vin_current.text().strip().upper()
                            and info.get("native_vin") == candidate):
                        message = "The new VIN matches the current VIN; nothing will be written."
                    else:
                        enabled = True
                        message = "Ready. Original data is saved and the VIN is verified after writing."
                        colour = "#9ece6a"
        self.btn_id_vin_apply.setEnabled(enabled)
        pairing_mapped = (self._is_ms42_ms43()
                          and self._ecu_cal_id in PAIRING_STATUS.get(self._ds2.family, {}))
        self.btn_virginize.setEnabled(ready and pairing_mapped)
        self.btn_virginize.setToolTip(
            "Clear pairing data, optionally VIN/AIF history. EWS alignment remains manual."
            if pairing_mapped else
            "Virginizing is unavailable: this software has no verified pairing restore/status check.")
        self.lbl_vin_validation.setText(message)
        self.lbl_vin_validation.setStyleSheet(f"color:{colour};")
        self._apply_transmission_recovery_quarantine()

    def _ms42_ms43_show_vin(self, info):
        from ms42_ms43_diagnostics import format_aif_record

        history_available = "aif_raw" in info
        self._ms42_ms43_vin_info = info
        self._id_labels["source"].setText("Current ECU • BMW AIF" if history_available else "Current ECU • Identity")
        self._id_labels["vin"].setText(info.get("vin") or ("Not recorded" if history_available else "Unavailable"))
        self._ecu_vin = info.get("vin") or ""
        self._info_labels["VIN"].setText(info.get("vin") or "Unavailable")
        self.id_vin_current.setText(info.get("vin") or "")
        self.id_vin_custom.clear()
        lines = [format_aif_record(record) for record in info.get("aif_records", ())]
        lines.extend(info.get("aif_warnings", ()))
        self.id_boot_strings.setPlainText("\n".join(lines) or (
            "No recorded AIF entries." if history_available else "VIN / AIF unavailable. Read again to retry."))
        self.lbl_identity_cache.setText("Current ECU • VIN and programming history" if history_available else
                                        "Current ECU • VIN / AIF unavailable")
        self.lbl_identity_cache.setStyleSheet(
            f"color:{'#9ece6a' if history_available else '#e8c46a'}; padding-left:8px;")
        self._ms42_ms43_update_vin()

    def _ms42_ms43_read_vin(self):
        if (self._task_busy or self._ms42_ms43_link_unavailable()
                or self._actuator_cleanup_pending):
            return
        from ms42_ms43_diagnostics import read_identity

        ecu = self._ds2

        def task(log_fn, progress_fn):
            info = read_identity(ecu)
            for warning in info.get("warnings", ()):
                log_fn(warning, "warn")
            return info

        def show(info):
            self._ms42_ms43_show_info(info)
            self._ms42_ms43_show_vin(info)

        self._run_task(task, on_success=show)

    def _ms42_ms43_write_vin(self):
        self._ms42_ms43_update_vin()
        if not self.btn_id_vin_apply.isEnabled():
            return
        from ms42_ms43_vin_write import write_vin

        ecu = self._ds2
        vin = self.id_vin_custom.text().strip().upper()
        voltage = self._prewrite_battery_notice()
        if QMessageBox.question(
                self, "Write VIN",
                f"Current VIN: {self.id_vin_current.text() or 'Not read'}\nNew VIN: {vin}\n\n"
                "A free AIF history slot is used when available. A full or interrupted history "
                "requires rewriting its 16 KB flash sector. Original data is saved first, "
                "and all other ECU data is preserved.\n\n"
                "Keep ignition ON, the engine STOPPED and power stable. Power loss during a "
                f"sector rewrite can require hardware recovery.\n\n{voltage}\n\nWrite this VIN?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return

        def task(log_fn, progress_fn):
            with ecu.transfer():
                return write_vin(ecu, vin, log_fn=log_fn, progress_cb=progress_fn)

        self._run_state_changing_task(task, on_success=self._ms42_ms43_vin_written,
                                      on_failure=self._ms42_ms43_vin_failed)

    def _ms42_ms43_vin_failed(self, error, *, virginizing=False):
        completed = getattr(error, "programming_completed", False)
        state = getattr(error, "state", None) or getattr(self._ds2, "write_state", None)
        if completed or getattr(state, "destructive_started", False):
            self._ecu_vin = None
            self._last_full_read = self._last_full_read_key = None
            self._ms42_ms43_vin_info = None
            self._ms42_ms43_identity_info = {}
            self._id_labels["isn"].setText("Unverified after identity write")
            for field in ("ECU Number", "EWS2 ISN", "ISN Data"):
                self._info_labels[field].setText("Unverified")
            self.id_vin_current.clear()
            status = "Unverified after virginizing" if virginizing else "Unverified after VIN write"
            self._id_labels["vin"].setText(status)
            self._info_labels["VIN"].setText(status)
            self._id_labels["source"].setText("Previous read • unverified")
            self.id_boot_strings.setPlainText("Current VIN and programming history require verification.")
            self.lbl_identity_cache.setText("VIN / AIF unverified")
            self.lbl_identity_cache.setStyleSheet("color:#e8c46a; padding-left:8px;")
            if virginizing:
                self._identity_isn = self._identity_isn_key = None
                self.id_ews_isn.clear()
                self.id_ews_frames.setPlainText("Virginizing requires verification. EWS alignment was not performed.")
        if completed:
            if virginizing:
                self._log("Virginize data was verified but follow-up failed. Do not repeat the write. "
                          "EWS alignment was not performed.", "warn")
            else:
                self._log("VIN programming completed. Reconnect if needed, then use Read VIN / AIF "
                          "to confirm it; do not repeat the write.", "warn")
            QMessageBox.warning(self, "Virginize — Follow-up Failed" if virginizing else
                                "VIN Programmed — Follow-up Failed", str(error))
        else:
            QMessageBox.critical(self, "Virginize Failed" if virginizing else "VIN Write Failed", str(error))

    def _ms42_ms43_virginize(self):
        if not self._is_ms42_ms43():
            return
        self._ms42_ms43_update_vin()
        if not self.btn_virginize.isEnabled():
            return
        from ms42_ms43_vin_write import virginize

        ecu = self._ds2
        voltage = self._prewrite_battery_notice()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Virginize ECU")
        box.setText(
            "Reset the ECU's ISN and pairing data?\n\n"
            "VIN and AIF programming history are preserved unless you select the option below. "
            "That option also erases recorded dates, software and workshop entries.\n\n"
            "Original data is saved before rewriting the 16 KB flash sector. "
            "All other ECU data is preserved.\n\n"
            "EWS alignment will not run. You can pair the ECU later with your preferred tool.\n\n"
            "Keep ignition ON, the engine STOPPED and power stable. Power loss during a "
            f"sector rewrite can require hardware recovery.\n\n{voltage}\n\nVirginize this ECU?")
        clear_vin_option = QCheckBox("Also clear VIN and programming history", box)
        box.setCheckBox(clear_vin_option)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec_() != QMessageBox.Yes:
            return
        clear_vin = clear_vin_option.isChecked()

        def task(log_fn, progress_fn):
            with ecu.transfer():
                return virginize(ecu, log_fn=log_fn, progress_cb=progress_fn, clear_vin=clear_vin)

        self._run_state_changing_task(task, on_success=self._ms42_ms43_virginized,
            on_failure=lambda error: self._ms42_ms43_vin_failed(error, virginizing=True))

    def _ms42_ms43_virginized(self, info):
        info = dict(info, isn=None, isn_status="Not stored")
        self._last_full_read = self._last_full_read_key = None
        self._identity_isn = self._identity_isn_key = None
        self.id_ews_isn.clear()
        identity_info = dict(getattr(self, "_ms42_ms43_identity_info", {}))
        for record in identity_info.get("aif_records", ()):
            for field in record:
                identity_info.pop(field, None)
        identity_info.update(info)
        self._ms42_ms43_show_info(identity_info)
        self._ms42_ms43_show_vin(info)
        message = ("ECU is already virginized; no write was needed." if info.get("already_virginized")
                   else "ECU virginized and verified.")
        message += "\nISN and pairing data are cleared."
        message += (" VIN and AIF programming history are cleared." if info.get("vin_cleared") else
                    " VIN and AIF programming history are preserved.")
        message += "\nEWS alignment was not performed. You can pair later with your preferred tool."
        self.id_ews_frames.setPlainText(message)
        self._log(message, "ok")
        QMessageBox.information(self, "ECU Virginized", message)

    def _ms42_ms43_vin_written(self, info):
        self._ecu_vin = info["vin"]
        self._last_full_read = None
        self._last_full_read_key = None
        identity_info = dict(getattr(self, "_ms42_ms43_identity_info", {}))
        identity_info.update(info)
        self._ms42_ms43_show_info(identity_info)
        self._ms42_ms43_show_vin(info)
        self._log(f"VIN written and verified: {info['vin']}", "ok")
        QMessageBox.information(self, "VIN Write Complete", f"VIN written and verified:\n\n{info['vin']}")

    def _ms42_ms43_read(self, mode):
        ecu = self._ds2
        size = images.FULL_SIZE if mode == "full" else ecu.profile.partial_size
        label = f"{'Full ROM image' if mode == 'full' else 'Calibration'} ({size // 1024} KB)"
        force_slow = self.chk_force_slow_ds2.isChecked()
        link = getattr(ecu, "transport", None)
        ram_candidate = (not force_slow
                         and hasattr(getattr(link, "_ser", None), "parity")
                         and self._ecu_cal_id in SUPPORTED_SOFTWARE[ecu.family])
        if ram_candidate and QMessageBox.question(
                self, "Temporary RAM Read", "Keep ignition ON and the engine STOPPED.\n\n"
                "This read temporarily suspends normal ECU operation and restarts the ECU "
                "when the read finishes.\n\nStart the read?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        source = "ECU diagnostic read"
        checksum_context = None

        def task(log_fn, progress_fn):
            nonlocal source, checksum_context
            with ecu.read_transfer(force_slow=force_slow, log_fn=log_fn, use_agent=not force_slow) as reader:
                source = getattr(reader, "read_source", "ECU diagnostic read")
                read = reader.read_full if mode == "full" else reader.read_tune
                data = bytes(read(progress_cb=progress_fn, log_fn=log_fn))
            if len(data) != size:
                raise ValueError(f"Read returned {len(data):,} bytes; expected {size:,}")
            checksum_context = None
            if mode == "tune" and ecu.family == "MS43":
                # read_transfer has restored normal diagnostics before this optional read.
                try:
                    checksum_context = ecu.read_memory(
                        images.MS43_MONITOR_CONTEXT_ADDRESS, images.MS43_MONITOR_CONTEXT_SIZE)
                except Exception as error:
                    log_fn(f"Calibration monitor context unavailable: {error}", "warn")
            for detail in images.checksum_status(data, context=checksum_context)["details"]:
                log_fn(detail)
            return data

        def done(data):
            entry = self._backup_save_bytes(data, mode, source, checksum_context=checksum_context)
            self._session_backup_read = True
            self._refresh_backup_table()
            self._offer_additional_read_copy(data, entry, label)
        self._run_task(task, on_success=done)

    def _ms42_ms43_choose_write(self, mode):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Open {self._ds2.family} {'Full Image' if mode == 'full' else 'Calibration'}",
            "", "Binary Files (*.bin);;All Files (*)")
        if path:
            try:
                self._ms42_ms43_write(Path(path).read_bytes(), Path(path).name, mode)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Image Not Loaded", str(error))

    def _ms42_ms43_write(self, data, filename, mode):
        ecu = self._ds2
        try:
            target_identity = images.image_identity(data, ecu.family)
            if mode == "full" and len(data) != images.FULL_SIZE:
                raise ValueError("Program and calibration writing requires a 512 KB full image.")
        except ValueError as error:
            QMessageBox.warning(self, "Incompatible Image", str(error))
            return
        verify = self.chk_verify.isChecked()
        backup = self.chk_backup_before_write.isChecked()
        force_slow = self.chk_force_slow_ds2.isChecked()
        write_boot = mode == "full" and self.chk_bootloader_write.isChecked()
        preserve_identity = self.chk_boot_preserve_identity.isChecked()
        if write_boot and force_slow:
            QMessageBox.warning(self, "Boot Update Unavailable", "Boot updates require the custom RAM agent.")
            return
        if write_boot:
            text, accepted = QInputDialog.getText(
                self, "BRICK-CLASS — Boot-Region Write",
                f"This will overwrite the {ecu.family} boot code via the RAM agent. "
                "An interrupted boot write can require hardware recovery.\n\n"
                "Type  WRITE BOOT  to proceed:")
            if not accepted or text.strip() != "WRITE BOOT":
                self._log("Full ROM write cancelled — boot-region confirmation declined.", "warn")
                return
        voltage = self._prewrite_battery_notice()
        identity_notice = ("Live VIN/AIF, ISN/pairing and ECU identifiers will be preserved."
                           if preserve_identity else
                           "VIN/AIF, ISN/pairing and ECU identifiers will be taken from the selected file.")
        boot_notice = ("Boot code WILL be updated and verified. " + identity_notice + "\n"
                       "The separate retained-data sectors are preserved.\n"
                       "Power loss during boot programming can require hardware recovery."
                       if write_boot else "Bootloader and protected identity are preserved.")
        answer = QMessageBox.question(
            self, f"Write {ecu.family}",
            f"Write {'program and calibration' if mode == 'full' else 'calibration'} "
            f"from {filename}?\n\n{boot_notice}\n"
            f"Backup: {'On' if backup else 'Off'} · Verify: {'On' if verify else 'Off'}\n\n"
            f"{voltage}\n\nKeep ignition ON, the engine STOPPED and the connection stable. "
            "The automatic transfer may restart the ECU to finalize programming.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        def task(log_fn, progress_fn):
            with ecu.transfer(force_slow=force_slow):
                if backup:
                    original = ecu.read_full(progress_cb=progress_fn, log_fn=log_fn)
                    self._backup_save_bytes(original, "full", "Before flash diagnostic read")
                write = ecu.write_full if mode == "full" else ecu.write_tune
                options = {"write_boot": write_boot} if mode == "full" else {}
                if write_boot:
                    options["preserve_identity"] = preserve_identity
                write(bytes(data), progress_cb=progress_fn, log_fn=log_fn, verify=verify,
                      use_ram=not force_slow, use_agent=not force_slow, **options)
            if not verify:
                log_fn("Program/calibration readback skipped (Verify off); boot readback verified."
                       if write_boot else
                       "Read-back verification skipped (Verify off). ECU-side finalization completed.")

        def done(_result):
            if mode == "full":
                self._ms42_ms43_written_revision(ecu, target_identity)
            self._refresh_backup_table()
            message = ("Programming completed. Turn ignition OFF, wait 10 seconds, "
                       "then turn ignition ON and reconnect.")
            self._log(message, "ok")
            self._show_flash_complete("Write Complete", "Programming completed. Reconnect after the ignition cycle.")
        self._run_state_changing_task(task, on_success=done,
            on_failure=(lambda error: self._ms42_ms43_full_write_failed(error, ecu, target_identity))
            if mode == "full" else None)

    def _ms42_ms43_written_revision(self, ecu, info):
        """Refresh revision-bound views from a completed full write, without rereading the ECU."""
        if self._ds2 is not ecu or self._ecu_cal_id == info["software_id"]:
            return
        self._invalidate_current_full_read("ECU software changed")
        self._identity_isn = self._identity_isn_key = None
        self._session_backup_read = False
        self._reset_live_config_state()
        self._clear_identity_tab_state()
        self._ecu_id = info["hardware_id"]
        self._ecu_cal_id = info["software_id"]
        self._ecu_vin = ""
        self._ecu_identity_source = None
        self._ms42_ms43_adapt_values.setRowCount(0)
        self._ms42_ms43_show_info(info)
        self._reset_diag_scan(True)
        self._ms42_ms43_controls()

    def _ms42_ms43_full_write_failed(self, error, ecu, target_identity):
        if getattr(error, "programming_completed", False):
            if getattr(getattr(error, "state", None), "completed", False):
                self._ms42_ms43_written_revision(ecu, target_identity)
            QMessageBox.warning(self, "Programming Completed — Follow-up Failed", str(error))
        else:
            QMessageBox.critical(self, "Operation Failed", str(error))

    def _ms42_ms43_adaptations(self):
        groups = self._ds2.adaptation_groups
        choice, accepted = QInputDialog.getItem(
            self, "Clear Adaptations", "Select learned values to reset:",
            ["All listed adaptations", *groups], 0, False)
        if not accepted:
            return
        selected = list(groups) if choice == "All listed adaptations" else [choice]
        if QMessageBox.question(
                self, "Clear Adaptations", f"Clear {choice.lower()}?\n"
                "The ECU will relearn these values during operation.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        def task(log_fn, progress_fn):
            self._ds2.clear_adaptations(selected)
            return f"{choice}: reset accepted."
        self._run_state_changing_task(task, on_success=lambda text: (
            self.lbl_adapt_status.setText(text), self._log(text, "ok")))

    def _ms42_ms43_ews(self, reset=False):
        label = "Reset EWS Start Value" if reset else "Synchronize EWS"
        if QMessageBox.question(
                self, label, f"{label} using the connected DME and EWS module?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        def task(log_fn, progress_fn):
            action = self._ds2.reset_ews_start_value if reset else self._ds2.sync_ews
            return action()
        def done(result):
            self.id_ews_frames.setPlainText(result["message"])
            self._log(result["message"])
        self._run_state_changing_task(task, on_success=done)

    def _ms42_ms43_read_faults(self, clear=False):
        from ms42_ms43_diagnostics import read_fault_memory, clear_fault_memory

        if clear and QMessageBox.question(
                self, "Clear Engine Faults", "Erase the stored engine fault memory?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        def task(log_fn, progress_fn):
            if clear:
                clear_fault_memory(self._ds2)
                log_fn("Clear accepted — reading fault memory to confirm.", "ok")
            try:
                return read_fault_memory(self._ds2), None
            except Exception as error:
                if not clear:
                    raise
                return None, str(error) or type(error).__name__
        def done(result):
            memory, error = result
            if memory is not None:
                self._fault_memories = {"stored": list(memory.stored), "shadow": list(memory.shadow)}
                self.cb_dtc_memory.setEnabled(True)
                self._show_dtc_memory()
            if clear:
                message = (f"Clear accepted — confirmation read failed: {error}" if error else
                           f"Clear accepted — {len(memory.stored)} stored, {len(memory.shadow)} shadow fault(s) remain.")
                self.lbl_dtc_count.setText(message)
                self.lbl_dtc_count.setStyleSheet("color:#e8c46a; padding:4px;" if error
                                                else "color:#5f5; padding:4px;")
                self._log(message, "warn" if error else "ok")
        def failed(error):
            self.lbl_dtc_count.setText("Clear not confirmed" if clear else "Read failed — no current results")
            self.lbl_dtc_count.setStyleSheet("color:#e8c46a; padding:4px;")
            QMessageBox.critical(self, "Fault Clear Failed" if clear else "Fault Read Failed", str(error))

        self._fault_memories = {}
        self.cb_dtc_memory.setEnabled(False)
        self.cb_dtc_memory.setCurrentIndex(0)
        self._show_dtc_memory()
        self.lbl_dtc_count.setText("Clearing faults…" if clear else "Reading faults…")
        self.lbl_dtc_count.setStyleSheet("color:#aaa; padding:4px;")
        runner = self._run_state_changing_task if clear else self._run_task
        if runner(task, on_success=done, on_failure=failed) is False:
            self.lbl_dtc_count.setText("Operation not started — see log")

    def _ms42_ms43_fault_detail(self):
        from ms42_ms43_diagnostics import format_fault_detail

        row = self.dtc_table.currentRow()
        records = self._dtcs
        if 0 <= row < len(records):
            self.dtc_detail.setPlainText(format_fault_detail(records[row]))

    def _ms42_ms43_export_faults(self):
        from ms42_ms43_diagnostics import format_fault_detail

        if not self._fault_memories:
            QMessageBox.information(self, "No Data", "Read DTCs first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export DTCs", "dtc_report.txt", "Text Files (*.txt)")
        if path:
            text = self._ds2.family + " engine fault records\n"
            for memory, records in self._fault_memories.items():
                text += f"\n{memory.title()} ({len(records)})\n"
                text += "\n\n".join(format_fault_detail(record) for record in records)
            try:
                Path(path).write_text(text, encoding="utf-8")
            except OSError as error:
                QMessageBox.warning(self, "Report Not Saved", str(error))

    def _ms42_ms43_build_data(self, layout, splitter):
        self._ms42_ms43_data_snapshot = None
        self._ms42_ms43_data_view = False
        self._ms42_ms43_data_body = QWidget()
        body = QVBoxLayout(self._ms42_ms43_data_body)
        body.setContentsMargins(0, 0, 0, 0)
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        note = QLabel("Read identity and programming history, or back up the memory selected below.")
        note.setWordWrap(True)
        controls_layout.addWidget(note)
        row = QHBoxLayout()
        self._ms42_ms43_data_read = self._op_btn("Read Identity / History", "#1e5080", self._ms42_ms43_read_data)
        self._ms42_ms43_data_load = self._op_btn("Load Report…", "#3d3d3d", self._ms42_ms43_load_data)
        self._ms42_ms43_data_save = self._op_btn("Save Report…", "#3d3d3d", self._ms42_ms43_save_data)
        self._ms42_ms43_data_close = self._op_btn("Clear Loaded Data", "#3d3d3d", self._ms42_ms43_close_data)
        self._ms42_ms43_data_read.setToolTip(
            "Read ECU identification, VIN, ISN and programming history through normal diagnostics. "
            "Memory backups are read separately below.")
        self._ms42_ms43_data_load.setToolTip(
            "Load a saved identity/history report. Older combined snapshots also load their flash backup.")
        self._ms42_ms43_data_save.setToolTip("Save the displayed identity/history report as JSON.")
        self._ms42_ms43_data_close.setToolTip(
            "Clear the report and loaded memory backup from this tab. Saved files are kept.")
        for button in (self._ms42_ms43_data_read, self._ms42_ms43_data_load,
                       self._ms42_ms43_data_save, self._ms42_ms43_data_close):
            row.addWidget(button)
        controls_layout.addLayout(row)
        self._ms42_ms43_build_storage(controls_layout)
        controls_layout.addStretch()
        self._ms42_ms43_data_controls = QScrollArea()
        self._ms42_ms43_data_controls.setFrameShape(QScrollArea.NoFrame)
        self._ms42_ms43_data_controls.setWidgetResizable(True)
        self._ms42_ms43_data_controls.setWidget(controls)
        self._ms42_ms43_data_splitter = splitter
        splitter.addWidget(self._ms42_ms43_data_controls)
        self._ms42_ms43_data_text = QPlainTextEdit()
        self._ms42_ms43_data_text.setReadOnly(True)
        self._ms42_ms43_data_text.setPlaceholderText(
            "Identity and programming history appear here.\n"
            "Use Inspect / Edit Backup to view the loaded memory backup.")
        self._ms42_ms43_data_text.setMinimumHeight(
            3 * self._ms42_ms43_data_text.fontMetrics().lineSpacing()
            + 2 * self._ms42_ms43_data_text.frameWidth())
        splitter.addWidget(self._ms42_ms43_data_text)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        body.addWidget(splitter, 1)
        layout.addWidget(self._ms42_ms43_data_body)
        self._ms42_ms43_data_body.hide()

    def _ms42_ms43_update_data(self):
        if not hasattr(self, "_ms42_ms43_data_body"):
            return
        connected = self._is_ms42_ms43()
        active = connected or self._ms42_ms43_data_view
        self._ms41_eeprom_body.setVisible(not active)
        self._ms42_ms43_data_body.setVisible(active)
        self.tabs.setTabText(self._eeprom_tab_index, "  ECU Data  " if active else "  EEPROM  ")
        idle = not self._task_busy and not self._actuator_cleanup_pending
        self._ms42_ms43_data_read.setEnabled(idle and connected and not self._ms42_ms43_link_unavailable())
        self._ms42_ms43_data_load.setEnabled(idle)
        self._ms42_ms43_data_save.setEnabled(idle and self._ms42_ms43_data_snapshot is not None)
        self._ms42_ms43_data_close.setEnabled(idle and (
            self._ms42_ms43_data_snapshot is not None or self._ms42_ms43_storage_snapshot is not None))
        self._ms42_ms43_update_storage()

    def _ms42_ms43_close_data(self):
        self._ms42_ms43_data_view = False
        self._ms42_ms43_data_snapshot = None
        self._ms42_ms43_storage_snapshot = None
        self._ms42_ms43_storage_source = ""
        self._ms42_ms43_storage_status.setText("No storage backup loaded.")
        self._ms42_ms43_data_text.clear()
        self._ms42_ms43_update_data()

    def _ms42_ms43_show_data(self, snapshot):
        from ms42_ms43_diagnostics import format_snapshot

        self._ms42_ms43_data_snapshot = snapshot
        self._ms42_ms43_data_view = True
        self._ms42_ms43_data_text.setPlainText(format_snapshot(snapshot))
        if "retained_raw" in snapshot:
            from ms42_ms43_storage import from_identity_snapshot
            try:
                self._ms42_ms43_show_storage(from_identity_snapshot(snapshot), "ECU Data snapshot")
            except ValueError as error:
                self._log(f"Retained flash can be inspected; restore metadata is unavailable: {error}", "warn")
        self._ms42_ms43_update_data()

    def _ms42_ms43_read_data(self):
        from ms42_ms43_diagnostics import read_identity

        def task(log_fn, progress_fn):
            log_fn("Reading ECU identity and programming history over stock diagnostics.")
            progress_fn(0, 1, "Identity / history")
            info = read_identity(self._ds2)
            progress_fn(1, 1, "Identity / history")
            return info

        self._run_task(task, on_success=self._ms42_ms43_show_data)

    def _ms42_ms43_load_data(self):
        from ms42_ms43_diagnostics import decode_snapshot

        path, _ = QFileDialog.getOpenFileName(self, "Load Identity / History Report", "", "ECU Reports (*.json)")
        if path:
            try:
                self._ms42_ms43_show_data(decode_snapshot(Path(path).read_text(encoding="utf-8")))
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Snapshot Unavailable", str(error))

    def _ms42_ms43_save_data(self):
        from ms42_ms43_diagnostics import encode_snapshot

        if self._ms42_ms43_data_snapshot is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Identity / History Report", "ecu_identity.json", "ECU Reports (*.json)")
        if path:
            try:
                Path(path).write_text(encode_snapshot(self._ms42_ms43_data_snapshot), encoding="utf-8")
                self._log(f"Identity/history report saved: {Path(path).name}", "ok")
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Snapshot Not Saved", str(error))
