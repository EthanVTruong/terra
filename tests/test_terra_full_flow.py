"""Production HTTP/feed boundaries backed by real offline weather and monitoring."""
from copy import deepcopy
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from terra_monitoring.demo_simulation.engine import DemoSimulation, ORCHARD, START
from terra_monitoring.demo_simulation.full_flow import ReplayBridge, create_app
from terra_monitoring.server import create_app as native_app
from terra_monitoring.farm_ops.service import orchard_profile
from terra_monitoring.farm_ops.store import utc


class FullFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = DemoSimulation(state_dir=self.root / "qa", grid_size=2)
        self.production, self.demo = self.root / "production", self.root / "lab"
        for directory, label in ((self.production, "production"), (self.demo, "station-lab")):
            (directory / "assets").mkdir(parents=True)
            (directory / "index.html").write_text(f'<main>{label}</main><script src="/assets/same.js"></script>', encoding="utf-8")
            (directory / "assets/same.js").write_text(label, encoding="utf-8")
        (self.demo / "assets/lab.css").write_text("@font-face{src:url('/xvf-reference.woff2')}", encoding="utf-8")
        (self.demo / "xvf-reference.woff2").write_bytes(b"demo-font")
        self.app = create_app(simulation=self.engine, web_root=self.production, demo_root=self.demo)
        self.bridge = self.app.state.full_flow_bridge
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8530")
        self.addCleanup(self.client.close)
        token = self.client.get("/api/sms/status").json()["control_token"]
        self.headers = {"X-Terra-SMS-Control": token, "Origin": "http://127.0.0.1:8530"}

    def create_farm(self, identity="qa-user", **geometry):
        response = self.client.post("/api/orchards", headers=self.headers,
            json={"name": "My walnut farm", "orchard_id": identity, **ORCHARD, **geometry})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def action(self, body):
        result = self.client.post("/api/demo/action", json=body, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def feed(self, identity="qa-user"):
        response = self.client.get(f"/api/orchards/{identity}/messages")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_production_and_lab_assets_are_separate_and_terra_is_actual_production(self):
        self.assertIn("production", self.client.get("/").text)
        self.assertEqual(self.client.get("/").content, self.client.get("/terra").content)
        lab = self.client.get("/stations").text
        self.assertIn("station-lab", lab)
        self.assertIn('src="/demo-assets/same.js"', lab)
        self.assertEqual(self.client.get("/assets/same.js").text, "production")
        self.assertEqual(self.client.get("/demo-assets/same.js").text, "station-lab")
        self.assertIn("/demo-static/xvf-reference.woff2", self.client.get("/demo-assets/lab.css").text)
        self.assertEqual(self.client.get("/demo-static/xvf-reference.woff2").content, b"demo-font")
        self.assertEqual(self.client.get("/api/not-a-real-route").status_code, 404)
        self.assertEqual(self.client.get("/demo-assets/%2e%2e/index.html").status_code, 404)

    def test_new_native_farm_is_injected_geometry_is_supplied_and_worker_is_not_faked(self):
        profile = self.create_farm(lat=38.57, lon=-121.79, elevation_ft=42.0)
        self.assertEqual(profile["mode"], "injected")
        self.assertEqual((profile["lat"], profile["lon"], profile["elevation_ft"]), (38.57, -121.79, 42.0))
        feed = self.feed()
        self.assertTrue(feed["monitor"]["comparison_available"])
        self.assertNotEqual(feed["monitor"]["worker"]["heartbeat_status"], "fresh")
        message = feed["messages"][0]
        details = self.client.get(f'/api/orchards/qa-user/messages/{message["id"]}/details').json()
        packet = details["assessments"][0]
        self.assertEqual(packet["data_mode"], "injected")
        self.assertEqual(packet["query"]["lat"], 38.57)
        self.assertEqual(packet["query"]["elevation_ft"], 42.0)
        self.assertFalse(self.client.get("/api/context").json()["configured"])

    def test_grower_profile_list_starts_empty_without_removing_station_lab_core(self):
        core_id = self.engine.session["orchard_id"]
        original_core = deepcopy(self.engine.store.orchard(core_id))
        original_notes = deepcopy(self.engine.store.messages(core_id))
        self.assertEqual(self.client.get("/api/orchards").json(), {"orchards": []})
        self.assertEqual(self.client.get(f"/api/orchards/{core_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/orchards/{core_id}/messages").status_code, 404)
        self.assertEqual(self.client.post(f"/api/orchards/{core_id}/questions", headers=self.headers,
            json={"question": "How much chill?", "request_id": "core-hidden"}).status_code, 404)
        user = self.create_farm(lat=38.57, elevation_ft=42.0)
        self.assertEqual(self.client.get("/api/orchards").json(), {"orchards": [user]})
        self.assertEqual(self.engine.store.orchard(core_id), original_core)
        self.assertEqual(self.engine.store.messages(core_id), original_notes)
        lab = self.client.get("/api/demo/state").json()
        self.assertEqual(lab["orchard"]["id"], core_id)
        self.assertEqual([m["id"] for m in lab["messages"]], [m["id"] for m in original_notes])

    def test_native_conversation_state_hides_core_and_preserves_supplied_profile(self):
        try:
            from terra_monitoring.conversation.service import ConversationService
        except ModuleNotFoundError:
            self.skipTest("Native Tara API has not been integrated into this branch yet")
        conversation = ConversationService(self.bridge, notification_capability="browser_open")
        state = conversation.state()
        self.assertEqual(state["orchards"], [])
        self.assertIsNone(state["orchard_id"])
        profile = self.bridge.create_orchard("Walnuts", orchard_id="crop-farm", lat=38.57, lon=-121.79,
            elevation_ft=42.0, crops=["walnut", "peach"], variety="Chandler")
        state = conversation.state(orchard_id=profile["id"])
        self.assertEqual([p["id"] for p in state["orchards"]], [profile["id"]])
        self.assertEqual(state["setup"]["crops"], ["walnut", "peach"])
        self.assertEqual(state["setup"]["variety"], "Chandler")
        self.assertEqual(state["setup"]["location"]["lat"], 38.57)
        with self.assertRaises(KeyError):
            conversation.state(orchard_id=self.engine.session["orchard_id"])

    def test_spike_flows_through_native_evidence_without_manufacturing_grower_alert(self):
        self.create_farm()
        original_ids = [m["id"] for m in self.feed()["messages"]]
        self.action({"type": "preset", "name": "spike"})
        feed = self.feed()
        self.assertEqual([m["id"] for m in feed["messages"]], original_ids)
        packet = self.bridge.assessment(self.bridge.store.orchard("qa-user"))
        self.assertEqual(packet["station_findings"][0]["station_id"], 6)
        self.assertNotIn(6, packet["location_estimate"]["used_station_ids"])
        self.assertTrue(feed["monitor"]["comparison_available"])

    def test_support_loss_native_feed_details_dedup_and_three_distinct_hour_recovery(self):
        self.create_farm()
        lab = self.action({"type": "preset", "name": "support_loss"})
        feed = self.feed()
        self.assertFalse(feed["monitor"]["comparison_available"])
        self.assertEqual(lab["assessment"]["location_estimate"]["status"], "insufficient_data")
        messages = [m for m in feed["messages"] if m["event_ids"] and any(
            e["type"] == "monitoring_limited" for e in self.bridge.store.events_by_ids("qa-user", m["event_ids"]))]
        self.assertEqual(len(messages), 1)
        note = messages[0]
        url = f'/api/orchards/qa-user/messages/{note["id"]}/details'
        immutable = self.client.get(url).json()["assessments"]
        self.assertEqual(immutable[0]["query"]["valid_utc"], START)
        self.action({"type": "preset", "name": "support_loss"})
        self.assertEqual([m["id"] for m in self.feed()["messages"]], [m["id"] for m in feed["messages"]])
        for _ in range(3):
            self.action({"type": "preset", "name": "normal"})
        self.assertFalse(any(e["type"] == "monitoring_recovered" for e in self.bridge.store.events("qa-user")))
        for expected in (1, 2, 3):
            self.action({"type": "advance", "hours": 1})
            self.assertEqual(self.bridge.store.get("watch:qa-user")["health_good_count"], expected)
            self.assertEqual(sum(e["type"] == "monitoring_recovered" for e in self.bridge.store.events("qa-user")), int(expected == 3))
        self.assertEqual(self.client.get(url).json()["assessments"], immutable)
        self.assertTrue(self.feed()["monitor"]["comparison_available"])

    def test_recovery_shortcut_evaluates_each_new_hour_for_all_saved_farms(self):
        self.create_farm()
        self.create_farm("qa-second", lat=38.56)
        self.action({"type": "preset", "name": "support_loss"})
        self.action({"type": "preset", "name": "recover"})
        for orchard in ("qa-user", "qa-second"):
            state = self.bridge.store.get("watch:" + orchard)
            self.assertEqual(state["health_good_count"], 3)
            self.assertIsNone(state["health_incident"])
            self.assertEqual(sum(e["type"] == "monitoring_recovered" for e in self.bridge.store.events(orchard)), 1)

    def test_core_profile_opt_in_rebinding_and_backwards_reset_are_rejected(self):
        core = deepcopy(self.engine.store.orchard(self.engine.session["orchard_id"]))
        self.assertEqual(self.client.post("/api/orchards", headers=self.headers,
            json={"orchard_id": core["id"], "name": "Replaced", **ORCHARD}).status_code, 422)
        self.create_farm()
        self.assertEqual(self.client.put("/api/orchards/qa-user/sms-opt-in", headers=self.headers,
            json={"enabled": True}).status_code, 422)
        self.assertEqual(self.client.post("/api/orchards", headers=self.headers,
            json={"orchard_id": "qa-user", "name": "Other farm", **ORCHARD}).status_code, 422)
        self.assertEqual(self.client.post("/api/demo/action", headers=self.headers, json={"type": "reset"}).status_code, 422)
        self.assertEqual(self.engine.store.orchard(core["id"]), core)
        self.assertEqual(self.client.post("/api/demo/action", headers=self.headers,
            json={"type": "advance", "hours": 1, "extra": "ignored?"}).status_code, 422)

    def test_no_live_dependencies_even_with_injected_environment_configuration(self):
        with patch("terra_monitoring.sms.SMSDispatcher.from_env", side_effect=AssertionError("read live SMS")), \
             patch("terra_monitoring.live.LiveQueryService", side_effect=AssertionError("read live weather")), \
             patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("external HTTP")), \
             patch("httpx.AsyncHTTPTransport.handle_async_request", side_effect=AssertionError("external HTTP")):
            app = create_app(simulation=self.engine, web_root=self.production, demo_root=self.demo)
            with TestClient(app, base_url="http://127.0.0.1:8531") as client:
                status = client.get("/api/sms/status").json()
                self.assertFalse(status["enabled"])
                response = client.post("/api/sms/demo", json={"event_id": "never-send"},
                    headers={"X-Terra-SMS-Control": status["control_token"]})
                self.assertEqual(response.json()["status"], "disabled")
                self.assertEqual(client.get("/api/health").status_code, 200)

    def test_cross_origin_actions_and_nonloopback_hosts_are_rejected(self):
        self.assertEqual(self.client.post("/api/demo/action", json={"type": "reset"},
            headers={"Origin": "https://other.invalid"}).status_code, 403)
        self.assertEqual(self.client.get("/", headers={"Host": "other.invalid"}).status_code, 400)
        self.assertEqual(self.client.post("/api/orchards", json={"name": "No guard", **ORCHARD}).status_code, 403)

    def test_crop_metadata_is_native_or_explicitly_withheld_until_tara_integration(self):
        if "crops" not in inspect.signature(orchard_profile).parameters:
            with self.assertRaisesRegex(ValueError, "integrated Tara"):
                self.bridge.create_orchard("Walnuts", orchard_id="crop-farm", crops=["walnut"], variety="Chandler", **ORCHARD)
            profile = self.create_farm()
            self.assertIsNone(profile["crop"])
            self.assertIsNone(profile["variety"])
        else:
            profile = self.bridge.create_orchard("Walnuts", orchard_id="crop-farm", crops=["walnut", "peach"], variety="Chandler", **ORCHARD)
            self.assertEqual(profile["crops"], ["walnut", "peach"])
            self.assertEqual(profile["variety"], "Chandler")
            self.assertIsNone(profile["target"]["hours"])

    def test_factory_refuses_other_demo_journal_and_reopens_own_marked_state(self):
        with self.assertRaisesRegex(ValueError, "another workflow"):
            create_app(state_dir=self.root / "qa", web_root=self.production)
        directory = self.root / "owned"
        app = create_app(state_dir=directory, web_root=self.production, demo_root=self.demo)
        run_id = app.state.full_flow_bridge.engine.session["run_id"]
        restored = create_app(state_dir=directory, web_root=self.production, demo_root=self.demo)
        self.assertEqual(restored.state.full_flow_bridge.engine.session["run_id"], run_id)

    def test_existing_native_conversation_and_routes_are_reused_without_fallback(self):
        native_conversation = {}

        def with_native_conversation(**kwargs):
            app = native_app(**kwargs)
            native_conversation["instance"] = app.state.tara_conversation
            return app

        def forbidden_fallback(*args, **kwargs):
            raise AssertionError("The bridge constructed a second conversation")

        with patch("terra_monitoring.demo_simulation.full_flow.production_app", with_native_conversation):
            app = create_app(simulation=self.engine, web_root=self.production, demo_root=self.demo,
                             conversation_factory=forbidden_fallback)
        self.assertIs(app.state.tara_conversation, native_conversation["instance"])
        def leaf_routes(routes):
            for route in routes:
                included = getattr(route, "original_router", None)
                if included is not None:
                    yield from leaf_routes(included.routes)
                else:
                    yield route

        routes = list(leaf_routes(app.router.routes))
        for path in ("/api/tara/conversation", "/api/tara/messages", "/api/tara/setup"):
            self.assertEqual(sum(getattr(route, "path", None) == path for route in routes), 1)
        with TestClient(app, base_url="http://127.0.0.1:8532") as client:
            state = client.get("/api/tara/conversation")
            self.assertEqual(state.status_code, 200)
            self.assertEqual(state.json()["schema_version"], "terra-conversation-v1")
            token = client.get("/api/sms/status").json()["control_token"]
            headers = {"X-Terra-SMS-Control": token}
            message = client.post("/api/tara/messages", headers=headers,
                json={"request_id": "reuse-native-thanks", "text": "Thanks"})
            self.assertEqual(message.status_code, 200, message.text)
            self.assertEqual(message.json()["outcome"], "none")
            setup = client.post("/api/tara/setup", headers=headers,
                json={"request_id": "reuse-native-restart", "action": "restart"})
            self.assertEqual(setup.status_code, 200, setup.text)
            self.assertEqual(setup.json()["state"]["setup"]["stage"], "crop")


if __name__ == "__main__":
    unittest.main()
