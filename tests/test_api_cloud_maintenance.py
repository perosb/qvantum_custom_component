"""Tests for Cloud maintenance and elevated-access operations."""

import datetime
from datetime import timedelta, timezone

import pytest


class TestCloudMaintenanceAccess:
    """Tests for the Cloud elevate-access workflow."""

    @pytest.mark.asyncio
    async def test_elevate_access_sufficient_level(self, authenticated_api):
        """An already sufficient access level requires no elevation."""
        access_data = {"writeAccessLevel": 25}
        cm, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=access_data
        )
        authenticated_api._session.get.return_value = cm

        result = await authenticated_api.elevate_access("test_device")

        assert result == access_data
        authenticated_api._session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_elevate_access_expires_soon(self, authenticated_api):
        """Access expiring within a day requires no re-elevation."""
        tomorrow = datetime.datetime.now(timezone.utc) + timedelta(hours=12)
        expires_at_str = tomorrow.isoformat().replace("+00:00", "Z")
        access_data = {"writeAccessLevel": 15, "expiresAt": expires_at_str}
        cm, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=access_data
        )
        authenticated_api._session.get.return_value = cm

        result = await authenticated_api.elevate_access("test_device")

        assert result == access_data
        authenticated_api._session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_elevate_access_expires_later(self, authenticated_api):
        """Access expiring later is renewed through the Cloud workflow."""
        future = datetime.datetime.now(timezone.utc) + timedelta(days=2)
        expires_at_str = future.isoformat().replace("+00:00", "Z")
        initial_access_data = {"writeAccessLevel": 15, "expiresAt": expires_at_str}
        generate_data = {"accessCode": "12345"}
        claim_data = {"message": "ok"}
        approve_data = {"status": "approved"}
        updated_access_data = {"writeAccessLevel": 25}

        cm1, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=initial_access_data
        )
        cm2, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )
        cm3, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=claim_data
        )
        cm4, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=approve_data
        )
        cm5, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=updated_access_data
        )

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            elif get_call_count == 2:
                return cm5
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        post_call_count = 0

        def post_side_effect(*args, **kwargs):
            nonlocal post_call_count
            post_call_count += 1
            if post_call_count == 1:
                return cm2
            elif post_call_count == 2:
                return cm3
            elif post_call_count == 3:
                return cm4
            else:
                raise ValueError(
                    f"Unexpected post call count: {post_call_count}, url: {args[0]}"
                )

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.side_effect = post_side_effect

        result = await authenticated_api.elevate_access("test_device")

        assert result == updated_access_data
        assert get_call_count == 2
        assert post_call_count == 3

    @pytest.mark.asyncio
    async def test_elevate_access_invalid_expires_at(self, authenticated_api):
        """An invalid expiry value follows the renewal workflow."""
        access_data = {"writeAccessLevel": 15, "expiresAt": "invalid-date"}
        generate_data = {"accessCode": "12345"}
        claim_data = {"message": "ok"}
        approve_data = {"status": "approved"}
        updated_access_data = {"writeAccessLevel": 25}

        cm1, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=access_data
        )
        cm2, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )
        cm3, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=claim_data
        )
        cm4, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=approve_data
        )
        cm5, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=updated_access_data
        )

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            elif get_call_count == 2:
                return cm5
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        post_call_count = 0

        def post_side_effect(*args, **kwargs):
            nonlocal post_call_count
            post_call_count += 1
            if post_call_count == 1:
                return cm2
            elif post_call_count == 2:
                return cm3
            elif post_call_count == 3:
                return cm4
            else:
                raise ValueError(
                    f"Unexpected post call count: {post_call_count}, url: {args[0]}"
                )

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.side_effect = post_side_effect

        result = await authenticated_api.elevate_access("test_device")

        assert result == updated_access_data
        assert get_call_count == 2
        assert post_call_count == 3

    @pytest.mark.asyncio
    async def test_elevate_access_insufficient_level(self, authenticated_api):
        """A low access level is elevated and then verified."""
        initial_access_data = {"writeAccessLevel": 15}
        generate_data = {"accessCode": "12345"}
        claim_data = {"message": "ok"}
        approve_data = {"status": "approved"}
        updated_access_data = {"writeAccessLevel": 25}

        cm1, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=initial_access_data
        )
        cm2, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )
        cm3, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=claim_data
        )
        cm4, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=approve_data
        )
        cm5, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=updated_access_data
        )

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            elif get_call_count == 2:
                return cm5
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        post_call_count = 0

        def post_side_effect(*args, **kwargs):
            nonlocal post_call_count
            post_call_count += 1
            if post_call_count == 1:
                return cm2
            elif post_call_count == 2:
                return cm3
            elif post_call_count == 3:
                return cm4
            else:
                raise ValueError(
                    f"Unexpected post call count: {post_call_count}, url: {args[0]}"
                )

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.side_effect = post_side_effect

        result = await authenticated_api.elevate_access("test_device")

        assert result == updated_access_data
        assert get_call_count == 2
        assert post_call_count == 3

    @pytest.mark.asyncio
    async def test_elevate_access_generate_code_failure(self, authenticated_api):
        """A failed code-generation request stops the workflow."""
        initial_access_data = {"writeAccessLevel": 15}
        cm1, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=initial_access_data
        )
        cm2, _ = authenticated_api._session.make_cm_response(
            status=400, json_data={"error": "Failed to generate code"}
        )

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.return_value = cm2

        result = await authenticated_api.elevate_access("test_device")

        assert result is None
        assert get_call_count == 1

    @pytest.mark.asyncio
    async def test_elevate_access_missing_access_code(self, authenticated_api):
        """A response without an access code stops the workflow."""
        initial_access_data = {"writeAccessLevel": 15}
        generate_data = {"someOtherField": "value"}
        cm1, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=initial_access_data
        )
        cm2, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.return_value = cm2

        result = await authenticated_api.elevate_access("test_device")

        assert result is None
        assert get_call_count == 1

    @pytest.mark.asyncio
    async def test_elevate_access_claim_grant_failure(self, authenticated_api):
        """A failed grant claim stops before approval."""
        initial_access_data = {"writeAccessLevel": 15}
        generate_data = {"accessCode": "12345"}
        cm1, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=initial_access_data
        )
        cm2, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )
        cm3, _ = authenticated_api._session.make_cm_response(
            status=400, json_data={"error": "Failed to claim grant"}
        )

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        post_call_count = 0

        def post_side_effect(*args, **kwargs):
            nonlocal post_call_count
            post_call_count += 1
            if post_call_count == 1:
                return cm2
            elif post_call_count == 2:
                return cm3
            else:
                raise ValueError(f"Unexpected post call count: {post_call_count}")

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.side_effect = post_side_effect

        result = await authenticated_api.elevate_access("test_device")

        assert result is None
        assert get_call_count == 1
        assert post_call_count == 2

    @pytest.mark.asyncio
    async def test_elevate_access_approve_failure(self, authenticated_api):
        """A failed approval stops before access is checked again."""
        initial_access_data = {"writeAccessLevel": 15}
        generate_data = {"accessCode": "12345"}
        claim_data = {"message": "ok"}
        cm1, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=initial_access_data
        )
        cm2, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )
        cm3, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=claim_data
        )
        cm4, _ = authenticated_api._session.make_cm_response(status=400)

        get_call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal get_call_count
            get_call_count += 1
            if get_call_count == 1:
                return cm1
            else:
                raise ValueError(f"Unexpected get call count: {get_call_count}")

        post_call_count = 0

        def post_side_effect(*args, **kwargs):
            nonlocal post_call_count
            post_call_count += 1
            if post_call_count == 1:
                return cm2
            elif post_call_count == 2:
                return cm3
            elif post_call_count == 3:
                return cm4
            else:
                raise ValueError(f"Unexpected post call count: {post_call_count}")

        authenticated_api._session.get.side_effect = get_side_effect
        authenticated_api._session.post.side_effect = post_side_effect

        result = await authenticated_api.elevate_access("test_device")

        assert result is None
        assert get_call_count == 1
        assert post_call_count == 3

    @pytest.mark.asyncio
    async def test_generate_code(self, authenticated_api):
        """Test the generate-code request."""
        generate_data = {"accessCode": "12345"}
        cm, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=generate_data
        )
        authenticated_api._session.post.return_value = cm

        result = await authenticated_api._generate_code("test_device")

        assert result == generate_data
        authenticated_api._session.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_claim_grant(self, authenticated_api):
        """Test the claim-grant request."""
        claim_data = {"message": "ok"}
        cm, _ = authenticated_api._session.make_cm_response(
            status=200, json_data=claim_data
        )
        authenticated_api._session.post.return_value = cm

        result = await authenticated_api._claim_grant("test_device", "12345")

        assert result is True
        authenticated_api._session.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_access(self, authenticated_api):
        """Test the approval request."""
        cm, _ = authenticated_api._session.make_cm_response(status=200)
        authenticated_api._session.post.return_value = cm

        result = await authenticated_api._approve_access("test_device", "12345")

        assert result is True
        authenticated_api._session.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_access_failure(self, authenticated_api):
        """Test a rejected approval request."""
        cm, _ = authenticated_api._session.make_cm_response(status=400)
        authenticated_api._session.post.return_value = cm

        result = await authenticated_api._approve_access("test_device", "12345")

        assert result is False
        authenticated_api._session.post.assert_called_once()
