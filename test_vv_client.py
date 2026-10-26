"""Offline tests - no network, no real credentials.
Run:  python -m unittest test_vv_client -v

The response shapes below are real V&V GetPartsInfo responses (trimmed):
a normal part (80040) and a subbed part (W11657965 -> W11831579).
"""
import os
import unittest
from unittest.mock import MagicMock, patch

import fitment
import vv_client as vv

NORMAL = {
    "return": {"retCode": "200", "retMsg": "Success"},
    "partData": {
        "partNumber": "80040", "mfgCode": "WHP", "subbed": "N",
        "partDescription": "DOG-AGIT", "quantityOnHand": "1646",
        "retailPrice": "4.490", "partPrice": "3.820", "discontinued": "N",
        "oversize": "N",
        "availableLocation": [
            {"locationId": "101", "locationName": "Youngstown", "availableQuantity": "956"},
            {"locationId": "103", "locationName": "Erie", "availableQuantity": "21"},
        ],
    },
    "subPartData": [],
}

SUBBED = {
    "return": {"retCode": "200", "retMsg": "Success"},
    "partData": {
        "partNumber": "W11657965", "mfgCode": "WHP", "subbed": "Y",
        "partDescription": "CCU ASM GILA2 120V NAR",
    },
    "subPartData": [{
        "partNumber": "W11831579", "mfgCode": "WHP", "subbed": "N",
        "partDescription": "CCU ASM GILA2 120V NAR", "quantityOnHand": "130",
        "retailPrice": "171.850", "partPrice": "146.070", "discontinued": "N",
        "oversize": [],  # V&V sends [] here instead of "N"
        "availableLocation": [
            {"locationId": "101", "locationName": "Youngstown", "availableQuantity": "86"},
        ],
    }],
}


def fake_response(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    return resp


ENV = {"VV_API_USER": "u", "VV_API_PASS": "p", "VV_API_TOKEN": "t"}


class Normalize(unittest.TestCase):
    def test_normal_part(self):
        info = vv.normalize_part_info(NORMAL)
        self.assertEqual(info["list_price"], 4.49)
        self.assertEqual(info["quantity_available"], 1646)
        self.assertEqual(len(info["locations"]), 2)
        self.assertFalse(info["subbed"])
        self.assertIs(vv.current_part(info), info)

    def test_dealer_cost_not_exposed(self):
        self.assertNotIn("partPrice", vv.normalize_part_info(NORMAL))

    def test_subbed_part_has_no_price_but_sub_does(self):
        info = vv.normalize_part_info(SUBBED)
        self.assertTrue(info["subbed"])
        self.assertIsNone(info["list_price"])
        sub = vv.current_part(info)
        self.assertEqual(sub["partNumber"], "W11831579")
        self.assertEqual(sub["list_price"], 171.85)

    def test_sub_chain_is_refused(self):
        chained = {"partData": {"partNumber": "A1", "subbed": "Y", "partDescription": "x"},
                   "subPartData": [{"partNumber": "B1", "subbed": "Y", "partDescription": "x"}]}
        self.assertIsNone(vv.current_part(vv.normalize_part_info(chained)))


class MfgGuess(unittest.TestCase):
    def test_prefix_guesses_come_first(self):
        self.assertEqual(vv.guess_mfg_codes("WPW10654005")[0], "WHP")
        self.assertEqual(vv.guess_mfg_codes("WB62X47094")[0], "GEN")

    def test_numeric_part_falls_back_to_primaries(self):
        self.assertEqual(vv.guess_mfg_codes("341241"), vv.PRIMARY_MFG_CODES)

    def test_no_duplicates(self):
        codes = vv.guess_mfg_codes("WPW10654005")
        self.assertEqual(len(codes), len(set(codes)))


@patch.dict(os.environ, ENV)
class Calls(unittest.TestCase):
    @patch("vv_client.requests.post")
    def test_wrong_code_then_right_code(self, post):
        invalid = {"return": {"retCode": "211", "retMsg": "Invalid mfgCode"}}
        post.side_effect = [fake_response(invalid), fake_response(NORMAL)]
        info = vv.get_part_info("80040")
        self.assertEqual(info["partNumber"], "80040")
        self.assertEqual(post.call_count, 2)

    @patch("vv_client.requests.post")
    def test_unknown_part_is_none_not_error(self, post):
        post.return_value = fake_response({"return": {"retCode": "211"}})
        self.assertIsNone(vv.get_part_info("NOPE123"))

    @patch("vv_client.requests.post")
    def test_given_mfg_code_is_the_only_one_tried(self, post):
        post.return_value = fake_response({"return": {"retCode": "211"}})
        vv.get_part_info("80040", "WHP")
        self.assertEqual(post.call_count, 1)

    @patch("vv_client.requests.post")
    def test_bad_login_raises(self, post):
        post.return_value = fake_response({"return": {"retCode": "201"}})
        with self.assertRaises(vv.VvAuthError):
            vv.get_part_info("80040")

    @patch("vv_client.requests.post")
    def test_http_500_raises_not_empty(self, post):
        post.return_value = fake_response({}, status=500)
        with self.assertRaises(vv.VvApiError):
            vv.part_to_models("341241")

    @patch("vv_client.requests.post")
    def test_part_to_models_non_list_raises_with_preview(self, post):
        post.return_value = fake_response({"message": "no data"})
        with self.assertRaises(vv.VvApiError) as ctx:
            vv.part_to_models("341241")
        self.assertIn("no data", str(ctx.exception))

    @patch("vv_client.requests.post")
    def test_batch_keeps_order_and_dedupes(self, post):
        def respond(url, json=None, **kw):
            pn = json["partNumber"]
            return fake_response(NORMAL if pn == "80040" else {"return": {"retCode": "211"}})
        post.side_effect = respond
        out = vv.get_parts_info_many(["80040", "NOPE123", "80040"], max_workers=2)
        self.assertEqual([r["status"] for r in out], ["ok", "not_found", "ok"])
        self.assertEqual([r["partNumber"] for r in out], ["80040", "NOPE123", "80040"])

    @patch("vv_client.requests.post")
    def test_batch_isolates_one_failed_part(self, post):
        def respond(url, json=None, **kw):
            if json["partNumber"] == "BAD1":
                return fake_response({}, status=500)
            return fake_response(NORMAL)
        post.side_effect = respond
        out = vv.get_parts_info_many(["80040", "BAD1"])
        self.assertEqual(out[0]["status"], "ok")
        self.assertEqual(out[1]["status"], "error")


class Fitment(unittest.TestCase):
    MODELS = [{"modelNumber": "WRS325FDAM04", "manufacturer": "WHIRLPOOL"},
              {"modelNumber": "YWRS325FDAM04", "manufacturer": "WHIRLPOOL"}]

    def test_check_fit(self):
        self.assertTrue(fitment.check_fit(self.MODELS, "wrs325fdam04")[0])
        self.assertFalse(fitment.check_fit(self.MODELS, "NOPE")[0])

    def test_close_matches_ignores_y_w_prefix(self):
        self.assertIn("WRS325FDAM04", fitment.close_matches(self.MODELS, "YWRS325FDAX9"))


if __name__ == "__main__":
    unittest.main()
