import importlib.util
import ctypes
import hashlib
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "tinytouch_helper", ROOT / "macos" / "tinytouch_helper.py"
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class SerialFramingTests(unittest.TestCase):
    def test_event_glued_behind_a_truncated_one_is_recovered(self):
        nonce = "c51a6405ed821b8fe1b574ee20c6d05f"
        intact = f"EV {nonce} 5 1 1 deadbeef"
        glued = f"EV d5327a6c756644e27{intact}"
        self.assertEqual(helper.resynchronize_event(glued), intact)

    def test_resynchronize_leaves_clean_lines_alone(self):
        for line in ("EV aabb 1 1 1 ccdd", "PONG", "OK STATUS firmware=unified", ""):
            self.assertEqual(helper.resynchronize_event(line), line)

    def test_event_v2_glued_behind_truncated_data_is_recovered(self):
        intact = f"EV2 {'ab' * 16} 5 1 1 deadbeefdeadbeef:{'cd' * 32}"
        self.assertEqual(helper.resynchronize_event(f"EV partial{intact}"), intact)


class CredentialPreflightTests(unittest.TestCase):
    def test_preflight_distinguishes_access_denial_from_corrupt_credentials(self):
        for error, exit_code in (
            (helper.KeychainError("read", -25293), 1),
            (ValueError("invalid key"), 2),
        ):
            with (
                self.subTest(error=type(error).__name__),
                mock.patch.object(
                    helper.sys, "argv", ["helper", "--check-credentials"]
                ),
                mock.patch.object(helper, "set_background_mode"),
                mock.patch.object(helper, "check_credentials", side_effect=error),
                mock.patch.object(helper, "diagnostic"),
            ):
                with self.assertRaises(SystemExit) as raised:
                    helper.main()
                self.assertEqual(raised.exception.code, exit_code)

    def test_connected_device_passes_despite_disconnected_cached_device(self):
        password, key = bytearray(b"test password"), bytearray(b"k" * 32)
        device_id = "TT-123456ABCDEF"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state-TT-000011112222.json").write_text("{}")
            (root / "settings-TT-000011112222.json").write_text("{}")
            with (
                mock.patch.object(helper, "STATE_DIR", root),
                mock.patch.object(
                    helper,
                    "device_endpoints",
                    return_value=[helper.DeviceEndpoint(device_id, "/dev/test", "")],
                ),
                mock.patch.object(helper, "credentials_exist", return_value=True),
                mock.patch.object(
                    helper, "load_passwords", return_value={0: password}
                ) as load,
                mock.patch.object(helper, "pairing_keychain_get", return_value=key),
                mock.patch.object(helper.serial, "Serial") as serial,
            ):
                helper.check_credentials()
        load.assert_called_once_with(device_id)
        self.assertEqual(password, bytearray(len(password)))
        self.assertEqual(key, bytearray(32))
        serial.assert_not_called()

    def test_offline_repair_can_find_saved_identities_without_usb(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state-TT-123456ABCDEF.json").write_text("{}")
            (root / "settings-TT-000011112222.json").write_text("{}")
            (root / "state-TT-invalid.json").write_text("{}")
            with (
                mock.patch.object(helper, "STATE_DIR", root),
                mock.patch.object(helper, "device_endpoints", return_value=[]),
                mock.patch.object(helper, "load_passwords") as load,
            ):
                self.assertEqual(
                    helper.known_device_ids(), {"TT-123456ABCDEF", "TT-000011112222"}
                )
                helper.check_credentials()
            load.assert_not_called()

    def test_upgrade_checks_denied_saved_credentials_while_disconnected(self):
        with (
            mock.patch.object(
                helper, "known_device_ids", return_value={"TT-123456ABCDEF"}
            ),
            mock.patch.object(helper, "device_endpoints", return_value=[]),
            mock.patch.object(helper, "credentials_exist", return_value=True),
            mock.patch.object(
                helper,
                "load_passwords",
                side_effect=helper.KeychainError("read", -25293),
            ),
        ):
            with self.assertRaises(helper.KeychainError):
                helper.check_credentials(include_saved=True)

    def test_pairing_denial_wipes_password_and_blocks_preflight(self):
        password = bytearray(b"test password")
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(helper, "STATE_DIR", Path(directory)),
            mock.patch.object(
                helper,
                "device_endpoints",
                return_value=[
                    helper.DeviceEndpoint("TT-123456ABCDEF", "/dev/test", "")
                ],
            ),
            mock.patch.object(helper, "credentials_exist", return_value=True),
            mock.patch.object(helper, "load_passwords", return_value={0: password}),
            mock.patch.object(
                helper,
                "pairing_keychain_get",
                side_effect=helper.KeychainError("read", -25293),
            ),
        ):
            with self.assertRaises(helper.KeychainError):
                helper.check_credentials()
        self.assertEqual(password, bytearray(len(password)))

    def test_unconfigured_device_does_not_block_first_setup(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(helper, "STATE_DIR", Path(directory)),
            mock.patch.object(
                helper,
                "device_endpoints",
                return_value=[
                    helper.DeviceEndpoint("TT-123456ABCDEF", "/dev/test", "")
                ],
            ),
            mock.patch.object(helper, "credentials_exist", return_value=False),
            mock.patch.object(helper, "load_passwords") as load,
        ):
            helper.check_credentials()
        load.assert_not_called()


class KeyboardMapCacheTests(unittest.TestCase):
    def test_live_layout_contents_invalidate_cached_translation(self):
        hitoolbox, foundation = mock.Mock(), mock.Mock()
        hitoolbox.TISCopyCurrentASCIICapableKeyboardLayoutInputSource.return_value = 123
        hitoolbox.TISGetInputSourceProperty.return_value = 456
        layouts = [ctypes.create_string_buffer(value) for value in (b"layout-a", b"layout-b")]
        foundation.CFDataGetBytePtr.side_effect = [ctypes.addressof(layouts[i]) for i in (0, 0, 1)]
        foundation.CFDataGetLength.return_value = 8

        def translate(layout, keycode, action, modifiers, keyboard_type, options, dead, size, actual, chars):
            actual._obj.value = 1
            chars[0] = ord("a" if layout.raw.startswith(b"layout-a") else "b")
            return 0

        hitoolbox.UCKeyTranslate.side_effect = translate
        helper._keyboard_output_map.cache_clear()
        try:
            with mock.patch.object(helper, "_keyboard_layout_libraries", return_value=(hitoolbox, foundation, None)):
                first = helper.current_keyboard_output_map()
                count = hitoolbox.UCKeyTranslate.call_count
                self.assertGreater(count, 90)
                first["mutation"] = "x"
                self.assertEqual(set(helper.current_keyboard_output_map()), {"a"})
                self.assertEqual(hitoolbox.UCKeyTranslate.call_count, count)
                self.assertEqual(set(helper.current_keyboard_output_map()), {"b"})
                self.assertEqual(hitoolbox.UCKeyTranslate.call_count, count * 2)
                self.assertEqual(foundation.CFRelease.call_count, 3)
                self.assertEqual(hitoolbox.TISCopyCurrentASCIICapableKeyboardLayoutInputSource.call_count, 3)
        finally:
            helper._keyboard_output_map.cache_clear()

    def test_missing_layout_releases_source_and_does_not_reuse_cache(self):
        hitoolbox, foundation = mock.Mock(), mock.Mock()
        hitoolbox.TISCopyCurrentASCIICapableKeyboardLayoutInputSource.return_value = 123
        hitoolbox.TISGetInputSourceProperty.return_value = None
        with mock.patch.object(helper, "_keyboard_layout_libraries", return_value=(hitoolbox, foundation, None)):
            with self.assertRaises(RuntimeError):
                helper.current_keyboard_output_map()
        foundation.CFRelease.assert_called_once_with(123)

    def test_empty_layout_is_not_passed_to_native_translation(self):
        hitoolbox, foundation = mock.Mock(), mock.Mock()
        hitoolbox.TISCopyCurrentASCIICapableKeyboardLayoutInputSource.return_value = 123
        hitoolbox.TISGetInputSourceProperty.return_value = 456
        foundation.CFDataGetBytePtr.return_value = 789
        foundation.CFDataGetLength.return_value = 0
        with mock.patch.object(helper, "_keyboard_layout_libraries", return_value=(hitoolbox, foundation, None)):
            with self.assertRaises(RuntimeError):
                helper.current_keyboard_output_map()
        hitoolbox.UCKeyTranslate.assert_not_called()
        foundation.CFRelease.assert_called_once_with(123)


class SerialDeliveryTests(unittest.TestCase):
    def test_fragmented_authenticated_requests_reach_the_wire_and_secrets_are_wiped(self):
        for version in (1, 2):
            with self.subTest(version=version):
                key = bytearray(range(32))
                original_key = bytes(key)
                secret = bytearray(b"abAa1")
                nonce = "12" * 16
                key_id = hashlib.sha256(key).hexdigest()[:16]
                if version == 1:
                    frame = f"EV {nonce} 1 2 99 {helper.mac_hex(key, f'EV|{nonce}|1|2|99')}\n"
                else:
                    material = f"EV2|{key_id}|{nonce}|1|2|99"
                    frame = f"EV2 {nonce} 1 2 99 {key_id}:{helper.mac_hex(key, material)}\n"
                bad_frame = f"EV {nonce} 1 2 99 {'00' * 32}\n"

                class Connection:
                    def __init__(self):
                        self.chunks = []
                        self.written = []

                    @property
                    def in_waiting(self):
                        return len(self.chunks[0]) if self.chunks else 0

                    def read(self, size):
                        if not self.chunks:
                            raise AssertionError("worker consumed every frame without responding")
                        self_test.assertLessEqual(size, len(self.chunks[0]))
                        result = self.chunks[0][:size]
                        self.chunks[0] = self.chunks[0][size:]
                        if not self.chunks[0]:
                            self.chunks.pop(0)
                        return result

                    def write(self, data):
                        self.written.append(data)
                        if data == b"STATUS\n":
                            self.chunks.extend([
                                b"OK STA", b"TUS firmware=unified\r\n", b"PONG 6\n\xff\n",
                                bad_frame.encode(), frame[:17].encode(), frame[17:-1].encode(), b"\n",
                            ])

                    def flush(self):
                        pass

                    def __enter__(self):
                        return self

                    def __exit__(self, *_):
                        pass

                self_test = self
                connection = Connection()
                device_id = "TT-001122334455"
                with (
                    mock.patch.object(helper, "open_serial", return_value=connection),
                    mock.patch.object(helper, "load_passwords", return_value={0: secret}),
                    mock.patch.object(helper, "pairing_keychain_get", return_value=key),
                    mock.patch.object(helper, "load_settings", return_value={"keyboard_layout": "us"}),
                    mock.patch.object(helper, "load_state", return_value={}),
                    mock.patch.object(helper, "REATTACHED_DEVICES", [device_id]),
                    mock.patch.object(helper, "device_ports", return_value=["/dev/cu.fake"]),
                    mock.patch.object(helper, "remember_nonce") as remember,
                    mock.patch.object(helper, "diagnostic"),
                    mock.patch("builtins.print"),
                ):
                    helper.serve_port("/dev/cu.fake", once=True, device_id=device_id)
                self.assertEqual(len(connection.written), 2)
                reply = connection.written[1].decode()
                self.assertEqual(HelperProtocolTests.decrypt_response(original_key, nonce, reply), b"abAa1")
                remember.assert_called_once_with({"seen_nonces": []}, nonce, device_id)
                self.assertFalse(any(secret))
                self.assertFalse(any(key))


class WorkerStateMachineTests(unittest.TestCase):
    def test_manager_failure_drains_workers_before_restart(self):
        endpoint = helper.DeviceEndpoint("TT-001122334455", "/dev/cu.fake", "1-1")
        stopped = threading.Event()
        workers = []
        original_worker = helper.Worker

        def worker_factory(endpoint):
            worker = original_worker(endpoint)
            workers.append(worker)
            return worker

        def serve(*_args, stop_event, **_kwargs):
            stop_event.wait(2)
            stopped.set()

        with (
            mock.patch.object(helper, "Worker", side_effect=worker_factory),
            mock.patch.object(helper, "serve_port", side_effect=serve),
            mock.patch.object(helper, "credentials_exist", return_value=True),
            mock.patch.object(helper.LeaseObserver, "active", return_value=None),
            mock.patch.object(helper, "device_endpoints",
                              side_effect=[[endpoint], OSError("discovery failed")]),
            mock.patch.object(helper.time, "sleep"),
            mock.patch.object(helper, "diagnostic"),
        ):
            with self.assertRaises(OSError):
                helper.run_manager()
        self.assertEqual(len(workers), 1)
        self.assertTrue(workers[0].stop_event.is_set())
        self.assertTrue(stopped.is_set())
        self.assertFalse(workers[0].thread.is_alive())

    def test_worker_does_not_retain_exception_frames(self):
        def fail(*_args, **_kwargs):
            try:
                raise ValueError("inner")
            except ValueError as exc:
                raise OSError("outer") from exc

        endpoint = helper.DeviceEndpoint("TT-001122334455", "/dev/cu.fake", "1-1")
        with mock.patch.object(helper, "serve_port", side_effect=fail):
            worker = helper.Worker(endpoint)
            worker.start()
            worker.thread.join()
        self.assertIsInstance(worker.error, OSError)
        self.assertIsNone(worker.error.__traceback__)
        self.assertIsNone(worker.error.__context__)
        self.assertIsNone(worker.error.__cause__)

    def test_worker_failure_has_explicit_terminal_phase(self):
        endpoint = helper.DeviceEndpoint("TT-001122334455", "/dev/cu.example", "1-1")
        with mock.patch.object(helper, "serve_port", side_effect=OSError("injected")):
            worker = helper.Worker(endpoint)
            self.assertEqual(worker.phase, helper.WorkerPhase.CREATED)
            worker.start()
            worker.thread.join()
        self.assertEqual(worker.phase, helper.WorkerPhase.FAILED)
        self.assertIsInstance(worker.error, OSError)

    def test_worker_drain_has_explicit_phase(self):
        endpoint = helper.DeviceEndpoint("TT-001122334455", "/dev/cu.example", "1-1")
        worker = helper.Worker(endpoint)
        worker.stop()
        self.assertEqual(worker.phase, helper.WorkerPhase.DRAINING)
        self.assertTrue(worker.stop_event.is_set())

    def test_worker_start_records_stability_window_origin(self):
        endpoint = helper.DeviceEndpoint("TT-001122334455", "/dev/cu.example", "1-1")
        with (
            mock.patch.object(helper, "serve_port"),
            mock.patch.object(helper.time, "monotonic", return_value=123.0),
        ):
            worker = helper.Worker(endpoint)
            worker.start()
            worker.thread.join()
        self.assertEqual(worker.started_at, 123.0)
        self.assertEqual(worker.phase, helper.WorkerPhase.STOPPED)

    def test_worker_carries_stable_identity_across_port_churn(self):
        endpoint = helper.DeviceEndpoint("TT-001122334455", "/dev/cu.renumbered", "1-1")
        with mock.patch.object(helper, "serve_port") as serve_port:
            worker = helper.Worker(endpoint)
            worker.start()
            worker.thread.join()
        serve_port.assert_called_once_with(
            "/dev/cu.renumbered",
            stop_event=worker.stop_event,
            device_id="TT-001122334455",
        )


class HelperProtocolTests(unittest.TestCase):
    def test_serial_setup_failure_closes_descriptor(self):
        connection = mock.Mock()
        connection.reset_input_buffer.side_effect = OSError("disconnected")
        with mock.patch.object(helper.serial, "Serial", return_value=connection):
            with self.assertRaises(OSError):
                helper.open_serial("/dev/cu.fake")
        connection.close.assert_called_once_with()

    def test_pairing_key_failure_wipes_loaded_password(self):
        secret = bytearray(b"password")
        with (
            mock.patch.object(helper, "load_passwords", return_value={0: secret}),
            mock.patch.object(helper, "pairing_keychain_get", side_effect=KeyError),
        ):
            with self.assertRaises(KeyError):
                helper.serve_port("/dev/cu.fake", device_id="TT-001122334455")
        self.assertFalse(any(secret))

    def test_state_failure_wipes_password_and_pairing_key(self):
        secret = bytearray(b"password")
        key = bytearray(range(32))
        with (
            mock.patch.object(helper, "load_passwords", return_value={0: secret}),
            mock.patch.object(helper, "pairing_keychain_get", return_value=key),
            mock.patch.object(helper, "load_state", side_effect=OSError),
        ):
            with self.assertRaises(OSError):
                helper.serve_port("/dev/cu.fake", device_id="TT-001122334455")
        self.assertFalse(any(secret))
        self.assertFalse(any(key))

    def test_password_overrides_cover_all_forty_template_ids(self):
        identity = "TT-123456ABCDEF"
        secret = bytearray(b"password")
        with (
            mock.patch.object(helper, "has_password", side_effect=lambda service, account: account.endswith(":40")) as exists,
            mock.patch.object(helper, "keychain_get", return_value=secret) as get,
        ):
            passwords = helper.load_passwords(identity)
        self.assertEqual(set(passwords), {0, 40})
        self.assertEqual(exists.call_count, 50)
        get.assert_any_call(identity + ":fingerprint:40")

    def test_partial_password_load_failure_wipes_previous_slots(self):
        secret = bytearray(b"password")
        with (
            mock.patch.object(helper, "keychain_get", return_value=secret),
            mock.patch.object(helper, "has_password", side_effect=OSError),
        ):
            with self.assertRaises(OSError):
                helper.load_passwords(helper.ACCOUNT)
        self.assertFalse(any(secret))

    @staticmethod
    def decrypt_response(key, nonce, response):
        parts = response.split()
        offset = 1 if parts[0] == "PW" else 2
        iv_hex, ciphertext_hex = parts[offset + 1], parts[offset + 2]
        return helper.aes_ctr_crypt(
            helper.session_key(key, nonce), bytes.fromhex(iv_hex),
            bytes.fromhex(ciphertext_hex),
        )

    def test_authenticated_event_returns_decryptable_password(self):
        key = bytes(range(32))
        password = b"correct horse battery staple!"
        nonce = "01" * 16
        event_mac = helper.mac_hex(key, f"EV|{nonce}|7|1|1")
        response = helper.handle_event(
            f"EV {nonce} 7 1 1 {event_mac}",
            password,
            key,
            {"seen_nonces": []},
            persist_state=False,
        )
        self.assertIsNotNone(response)
        kind, got_nonce, iv_hex, ciphertext_hex, response_mac = response.split()
        self.assertEqual((kind, got_nonce), ("PW", nonce))
        self.assertEqual(
            response_mac,
            helper.mac_hex(key, f"PW|{nonce}|{iv_hex}|{ciphertext_hex}"),
        )
        plaintext = helper.aes_ctr_crypt(
            helper.session_key(key, nonce), bytes.fromhex(iv_hex), bytes.fromhex(ciphertext_hex)
        )
        self.assertEqual(plaintext, password)

    def test_wipeable_secret_buffers_are_supported(self):
        key = bytearray(range(32))
        password = bytearray(b"wipe me")
        nonce = "0d" * 16
        event_mac = helper.mac_hex(key, f"EV|{nonce}|1|1|1")
        response = helper.handle_event(
            f"EV {nonce} 1 1 1 {event_mac}",
            password,
            key,
            {"seen_nonces": []},
            persist_state=False,
        )
        self.assertEqual(self.decrypt_response(key, nonce, response), password)

    def test_commoncrypto_matches_nist_aes_256_ctr_vector(self):
        key = bytes.fromhex(
            "603deb1015ca71be2b73aef0857d7781"
            "1f352c073b6108d72d9810a30914dff4"
        )
        iv = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
        plaintext = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        expected = bytes.fromhex("601ec313775789a5b7a7f504bbf3d228")
        self.assertEqual(helper.aes_ctr_crypt(key, iv, plaintext), expected)

    def test_replayed_nonce_is_rejected(self):
        key = bytes(range(32))
        nonce = "02" * 16
        event_mac = helper.mac_hex(key, f"EV|{nonce}|1|1|1")
        state = {"seen_nonces": [nonce]}
        response = helper.handle_event(
            f"EV {nonce} 1 1 1 {event_mac}",
            b"password",
            key,
            state,
            persist_state=False,
        )
        self.assertIsNone(response)

    def test_nonce_replay_is_case_insensitive(self):
        key = bytes(range(32))
        nonce = "AB" * 16
        event_mac = helper.mac_hex(key, f"EV|{nonce}|1|1|1")
        response = helper.handle_event(
            f"EV {nonce} 1 1 1 {event_mac}",
            b"password",
            key,
            {"seen_nonces": [nonce.lower()]},
            persist_state=False,
        )
        self.assertIsNone(response)

    def test_nonce_can_be_recorded_after_serial_delivery(self):
        key = bytes(range(32))
        nonce = "0c" * 16
        state = {"seen_nonces": []}
        event_mac = helper.mac_hex(key, f"EV|{nonce}|1|1|1")
        response = helper.handle_event(
            f"EV {nonce} 1 1 1 {event_mac}",
            b"password",
            key,
            state,
            persist_state=False,
            record_nonce=False,
        )
        self.assertIsNotNone(response)
        self.assertEqual(state["seen_nonces"], [])
        with mock.patch.object(helper, "save_state") as save_state:
            helper.remember_nonce(state, nonce, "DEVICE")
        self.assertEqual(state["seen_nonces"], [nonce])
        save_state.assert_called_once_with(state, "DEVICE")

    def test_v2_event_selects_this_computers_independent_key(self):
        key = bytes(range(32))
        password = b"a different Mac password"
        nonce = "03" * 16
        key_id = hashlib.sha256(key).hexdigest()[:16]
        event_mac = helper.mac_hex(key, f"EV2|{key_id}|{nonce}|8|1|77")
        response = helper.handle_event(
            f"EV2 {nonce} 8 1 77 deadbeefdeadbeef:{'00' * 32} {key_id}:{event_mac}",
            password,
            key,
            {"seen_nonces": []},
            persist_state=False,
        )
        self.assertIsNotNone(response)
        kind, got_id, got_nonce, iv_hex, ciphertext_hex, response_mac = response.split()
        self.assertEqual((kind, got_id, got_nonce), ("PW2", key_id, nonce))
        self.assertEqual(
            response_mac,
            helper.mac_hex(key, f"PW2|{key_id}|{nonce}|{iv_hex}|{ciphertext_hex}"),
        )
        plaintext = helper.aes_ctr_crypt(
            helper.session_key(key, nonce), bytes.fromhex(iv_hex), bytes.fromhex(ciphertext_hex)
        )
        self.assertEqual(plaintext, password)

    def test_v2_event_for_another_computer_is_ignored(self):
        response = helper.handle_event(
            f"EV2 {'04' * 16} 1 1 1 deadbeefdeadbeef:{'00' * 32}",
            b"password",
            bytes(range(32)),
            {"seen_nonces": []},
            persist_state=False,
        )
        self.assertIsNone(response)

    def test_parser_rejects_duplicate_or_excess_authenticators(self):
        key = bytes(range(32))
        nonce = "05" * 16
        key_id = hashlib.sha256(key).hexdigest()[:16]
        event_mac = helper.mac_hex(key, f"EV2|{key_id}|{nonce}|1|1|1")
        authenticator = f"{key_id}:{event_mac}"
        self.assertIsNone(helper.parse_event(
            f"EV2 {nonce} 1 1 1 {authenticator} {authenticator}", key
        ))
        extras = " ".join(f"{index:016x}:{'00' * 32}" for index in range(9))
        self.assertIsNone(helper.parse_event(f"EV2 {nonce} 1 1 1 {extras}", key))

    def test_parser_rejects_noncanonical_numbers_and_ranges(self):
        key = bytes(range(32))
        nonce = "06" * 16
        for counter, slot, score in (
            ("-1", "1", "1"),
            (str(1 << 64), "1", "1"),
            ("1", "0", "1"),
            ("1", "1", str(1 << 31)),
        ):
            self.assertIsNone(
                helper.parse_event(f"EV {nonce} {counter} {slot} {score} {'00' * 32}", key)
            )

    def test_matches_from_all_forty_templates_are_authenticated(self):
        key = bytes(range(32))
        nonce = "09" * 16
        for slot in (1, 5, 6, 39, 40):
            signature = helper.mac_hex(key, f"EV|{nonce}|1|{slot}|42")
            event = helper.parse_event(f"EV {nonce} 1 {slot} 42 {signature}", key)
            self.assertIsNotNone(event)
            self.assertEqual(event.slot, slot)
        signature = helper.mac_hex(key, f"EV|{nonce}|1|41|42")
        self.assertIsNone(helper.parse_event(f"EV {nonce} 1 41 42 {signature}", key))

    def test_fingerprint_slot_selects_override_and_falls_back_to_default(self):
        key = bytes(range(32))
        passwords = {0: b"default", 5: b"fifth finger"}
        for slot, expected in ((5, b"fifth finger"), (2, b"default")):
            nonce = f"{slot:02x}" * 16
            event_mac = helper.mac_hex(key, f"EV|{nonce}|1|{slot}|42")
            response = helper.handle_event(
                f"EV {nonce} 1 {slot} 42 {event_mac}", passwords, key,
                {"seen_nonces": []}, persist_state=False,
            )
            self.assertEqual(self.decrypt_response(key, nonce, response), expected)

    def test_layout_translation_happens_before_encryption(self):
        key = bytes(range(32))
        nonce = "0a" * 16
        event_mac = helper.mac_hex(key, f"EV|{nonce}|1|1|42")
        response = helper.handle_event(
            f"EV {nonce} 1 1 42 {event_mac}", b";", key,
            {"seen_nonces": []}, persist_state=False,
            keyboard_map={";": "<"},
        )
        self.assertEqual(self.decrypt_response(key, nonce, response), b"<")

    def test_unsupported_layout_character_refuses_complete_event(self):
        key = bytes(range(32))
        nonce = "0b" * 16
        event_mac = helper.mac_hex(key, f"EV|{nonce}|1|1|42")
        state = {"seen_nonces": []}
        response = helper.handle_event(
            f"EV {nonce} 1 1 42 {event_mac}", "é".encode(), key, state,
            persist_state=False, keyboard_map={"e": "e"},
        )
        self.assertIsNone(response)
        self.assertEqual(state["seen_nonces"], [])

    def test_serial_framing_preserves_split_and_multiple_events(self):
        lines, remainder = helper.split_serial_lines(b"EV2 abc", b" def\nPONG\nEV x")
        self.assertEqual(lines, [b"EV2 abc def", b"PONG"])
        self.assertEqual(remainder, b"EV x")
        lines, remainder = helper.split_serial_lines(remainder, b" y\n")
        self.assertEqual(lines, [b"EV x y"])
        self.assertEqual(remainder, b"")

    def test_serial_framing_drops_oversized_incomplete_line(self):
        lines, remainder = helper.split_serial_lines(
            b"", b"x" * (helper.MAX_SERIAL_LINE_BYTES + 1)
        )
        self.assertEqual(lines, [])
        self.assertEqual(remainder, b"")

    def test_oversized_password_is_refused(self):
        with self.assertRaises(ValueError):
            helper.translate_password(b"x" * 161, None)

    def test_stale_cli_suspension_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            suspend = Path(directory) / "suspend"
            acknowledgement = Path(directory) / "ack"
            suspend.write_text("999999\n")
            acknowledgement.write_text("old\n")
            with (
                mock.patch.object(helper, "SUSPEND_PATH", suspend),
                mock.patch.object(helper, "SUSPEND_ACK_PATH", acknowledgement),
                mock.patch.object(helper.os, "kill", side_effect=ProcessLookupError),
            ):
                helper.wait_for_cli_suspension()
            self.assertFalse(suspend.exists())
            self.assertFalse(acknowledgement.exists())


if __name__ == "__main__":
    unittest.main()
