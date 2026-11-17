import json
import string
import sys
import tempfile
import types
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest


# ---------------------------------------------------------------------------
# Fake credentials module
# ---------------------------------------------------------------------------

fake_credentials = types.ModuleType("credentials")
fake_credentials.get_password = (
    lambda *args, **kwargs: "test-only"
)

sys.modules.setdefault(
    "credentials",
    fake_credentials,
)


from automation import rotate_password as rp


# ---------------------------------------------------------------------------
# normalize_ip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        (
            "172.20.20.6",
            "172.20.20.6",
        ),
        (
            "172.20.20.6/24",
            "172.20.20.6",
        ),
        (
            "2001:db8::1/64",
            "2001:db8::1",
        ),
        (
            "",
            None,
        ),
        (
            None,
            None,
        ),
    ],
)
def test_normalize_ip_valid(
    value,
    expected,
):

    assert (
        rp.normalize_ip(
            value
        )
        == expected
    )


@pytest.mark.parametrize(
    "value",
    [
        "999.999.999.999",
        "not-an-ip",
        "192.168.1.1/33",
    ],
)
def test_normalize_ip_invalid(
    value,
):

    with pytest.raises(
        ValueError
    ):

        rp.normalize_ip(
            value
        )


# ---------------------------------------------------------------------------
# Password generation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "length",
    [
        16,
        24,
        32,
    ],
)
def test_generate_password(
    length,
):

    password = (
        rp.generate_password(
            length
        )
    )

    assert (
        len(password)
        == length
    )

    assert any(
        c.islower()
        for c
        in password
    )

    assert any(
        c.isupper()
        for c
        in password
    )

    assert any(
        c.isdigit()
        for c
        in password
    )

    allowed = (
        string.ascii_letters
        + string.digits
        + "_-."
    )

    assert all(
        c in allowed
        for c
        in password
    )


def test_generate_password_retry():

    characters = iter(
        "a" * 8
        + "Aa123456"
    )

    with patch.object(
        rp.secrets,
        "choice",
        side_effect=lambda alphabet:
            next(characters),
    ) as mock_choice:

        password = (
            rp.generate_password(
                8
            )
        )

    assert (
        password
        == "Aa123456"
    )

    assert (
        mock_choice.call_count
        == 16
    )


# ---------------------------------------------------------------------------
# Rotation log
# ---------------------------------------------------------------------------

def test_write_rotation_log(
    tmp_path,
    monkeypatch,
):

    log_file = (
        tmp_path
        / "rotation.log"
    )

    monkeypatch.setattr(
        rp,
        "ROTATION_LOG",
        log_file,
    )

    rp.write_rotation_log(
        "R1",
        "success",
    )

    rp.write_rotation_log(
        "R3",
        "failed",
        "SSH timeout",
    )

    lines = (
        log_file
        .read_text()
        .splitlines()
    )

    assert len(lines) == 2

    assert (
        "device=R1 status=success"
        in lines[0]
    )

    assert (
        "device=R3 status=failed"
        in lines[1]
    )

    assert (
        "message=SSH timeout"
        in lines[1]
    )


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "manufacturer,expected",
    [
        (
            "arista",
            "arista",
        ),
        (
            "nokia",
            "nokia",
        ),
    ],
)
def test_detect_platform_valid(
    manufacturer,
    expected,
):

    assert (
        rp.detect_platform(
            {
                "manufacturer":
                    manufacturer
            }
        )
        == expected
    )


def test_detect_platform_unsupported():

    with pytest.raises(
        RuntimeError,
        match="Unsupported manufacturer",
    ):

        rp.detect_platform(
            {
                "manufacturer":
                    "cisco"
            }
        )


# ---------------------------------------------------------------------------
# NetBox API
# ---------------------------------------------------------------------------

def test_netbox_missing_token():

    with patch.object(
        rp,
        "NETBOX_TOKEN",
        "",
    ):

        with pytest.raises(
            RuntimeError,
            match="NETBOX_TOKEN",
        ):

            rp.netbox_get(
                "/api/dcim/devices/"
            )


def test_netbox_success():

    data = {
        "results": [
            {
                "name": "R1"
            }
        ]
    }

    response = MagicMock()

    response.read.return_value = (
        json.dumps(
            data
        ).encode()
    )

    context = MagicMock()

    context.__enter__.return_value = (
        response
    )

    with patch.object(
        rp,
        "NETBOX_TOKEN",
        "test-token",
    ), patch.object(
        rp,
        "urlopen",
        return_value=context,
    ) as mock_urlopen:

        result = rp.netbox_get(
            "/api/dcim/devices/"
        )

    assert result == data

    request = (
        mock_urlopen
        .call_args
        .args[0]
    )

    assert (
        request.get_header(
            "Authorization"
        )
        == "Bearer test-token"
    )

    assert (
        mock_urlopen
        .call_args
        .kwargs["timeout"]
        == 10
    )


def test_netbox_network_error():

    with patch.object(
        rp,
        "NETBOX_TOKEN",
        "test-token",
    ), patch.object(
        rp,
        "urlopen",
        side_effect=URLError(
            "Connection refused"
        ),
    ):

        with pytest.raises(
            URLError
        ):

            rp.netbox_get(
                "/api/dcim/devices/"
            )


# ---------------------------------------------------------------------------
# Device inventory
# ---------------------------------------------------------------------------

def make_netbox_device(
    ip="172.20.20.6/24",
    username="admin",
    secret_ref="test-secret",
):

    return {
        "primary_ip4":
            {
                "address": ip
            }
            if ip
            else None,

        "primary_ip6":
            None,

        "custom_fields": {
            "management_username":
                username,

            "secret_ref":
                secret_ref,

            "credential_profile":
                "lab",
        },

        "device_type": {
            "manufacturer": {
                "name": "Arista"
            }
        },
    }


def make_rotation_device(
    manufacturer="arista",
    username="admin",
):

    return {
        "name": "R1",
        "mgmt_ip": "192.0.2.1",
        "username": username,
        "secret_ref": "test-secret",
        "credential_profile": "lab",
        "manufacturer": manufacturer,
    }


def test_get_device_success():

    device = (
        make_netbox_device()
    )

    with patch.object(
        rp,
        "netbox_get",
        return_value={
            "results": [
                device
            ]
        },
    ) as mock_netbox:

        result = (
            rp.get_device(
                "R1"
            )
        )

    assert result == {
        "name": "R1",
        "mgmt_ip": "172.20.20.6",
        "username": "admin",
        "secret_ref": "test-secret",
        "credential_profile": "lab",
        "manufacturer": "arista",
    }

    mock_netbox.assert_called_once_with(
        "/api/dcim/devices/?name=R1"
    )


@pytest.mark.parametrize(
    "results",
    [
        [],
        [
            {},
            {},
        ],
    ],
)
def test_get_device_not_unique(
    results,
):

    with patch.object(
        rp,
        "netbox_get",
        return_value={
            "results": results
        },
    ):

        with pytest.raises(
            RuntimeError,
            match=(
                "Expected one "
                "NetBox device"
            ),
        ):

            rp.get_device(
                "R1"
            )


@pytest.mark.parametrize(
    "ip,username,secret_ref,error",
    [
        (
            None,
            "admin",
            "test-secret",
            "no primary management IP",
        ),
        (
            "172.20.20.6/24",
            "",
            "test-secret",
            "management_username",
        ),
        (
            "172.20.20.6/24",
            "admin",
            "",
            "secret_ref",
        ),
    ],
)
def test_get_device_invalid(
    ip,
    username,
    secret_ref,
    error,
):

    device = (
        make_netbox_device(
            ip,
            username,
            secret_ref,
        )
    )

    with patch.object(
        rp,
        "netbox_get",
        return_value={
            "results": [
                device
            ]
        },
    ):

        with pytest.raises(
            RuntimeError,
            match=error,
        ):

            rp.get_device(
                "R1"
            )


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

def test_connect_success():

    with patch.object(
        rp.paramiko,
        "SSHClient",
    ) as mock_client:

        result = (
            rp.connect(
                "172.20.20.6",
                "admin",
                "fake-password",
            )
        )

    client = (
        mock_client
        .return_value
    )

    client.set_missing_host_key_policy \
        .assert_called_once()

    client.connect \
        .assert_called_once_with(
            hostname="172.20.20.6",
            username="admin",
            password="fake-password",
            look_for_keys=False,
            allow_agent=False,
            timeout=10,
            auth_timeout=10,
            banner_timeout=10,
        )

    assert result is client


def test_connect_auth_failure():

    with patch.object(
        rp.paramiko,
        "SSHClient",
    ) as mock_client:

        mock_client \
            .return_value \
            .connect \
            .side_effect = (
                rp.paramiko
                .AuthenticationException()
            )

        with pytest.raises(
            rp.paramiko
            .AuthenticationException
        ):

            rp.connect(
                "172.20.20.6",
                "admin",
                "fake-password",
            )


def test_read_channel_success():

    channel = MagicMock()

    channel.recv_ready.side_effect = [
        True,
        True,
        False,
    ]

    channel.recv.side_effect = [
        b"Hello ",
        b"World",
    ]

    with patch.object(
        rp.time,
        "sleep",
    ) as mock_sleep:

        result = (
            rp.read_channel(
                channel,
                delay=0,
            )
        )

    assert (
        result
        == "Hello World"
    )

    assert (
        channel.recv.call_count
        == 2
    )

    assert (
        mock_sleep.call_count
        == 3
    )


def test_read_channel_empty():

    channel = MagicMock()

    channel.recv_ready \
        .return_value = False

    with patch.object(
        rp.time,
        "sleep",
    ):

        result = (
            rp.read_channel(
                channel,
                delay=0,
            )
        )

    assert result == ""

    channel.recv \
        .assert_not_called()


def test_read_channel_invalid_utf8():

    channel = MagicMock()

    channel.recv_ready.side_effect = [
        True,
        False,
    ]

    channel.recv.return_value = (
        b"Hello\xffWorld"
    )

    with patch.object(
        rp.time,
        "sleep",
    ):

        result = (
            rp.read_channel(
                channel,
                delay=0,
            )
        )

    assert (
        result
        == "Hello\ufffdWorld"
    )


def test_send_command():

    channel = MagicMock()

    with patch.object(
        rp,
        "read_channel",
        return_value="R1#",
    ) as mock_read:

        result = (
            rp.send_command(
                channel,
                "show version",
                delay=1.5,
            )
        )

    channel.send \
        .assert_called_once_with(
            "show version\n"
        )

    mock_read \
        .assert_called_once_with(
            channel,
            1.5,
        )

    assert result == "R1#"


# ---------------------------------------------------------------------------
# open_old_session
# ---------------------------------------------------------------------------

def test_open_old_session_normal():

    device = (
        make_rotation_device()
    )

    client = MagicMock()

    with patch.object(
        rp,
        "get_password",
        return_value="OldTest123",
    ), patch.object(
        rp,
        "connect",
        return_value=client,
    ) as mock_connect:

        result = (
            rp.open_old_session(
                device,
                "arista",
                bootstrap=False,
            )
        )

    assert result == (
        client,
        "OldTest123",
        "secret-store",
    )

    mock_connect.assert_called_once_with(
        "192.0.2.1",
        "admin",
        "OldTest123",
    )


def test_open_old_session_bootstrap_fallback():

    device = (
        make_rotation_device()
    )

    client = MagicMock()

    def get_password(
        secret_ref,
    ):

        if (
            secret_ref
            == "test-secret"
        ):
            return "StaleTest123"

        return "Bootstrap123"

    with patch.object(
        rp,
        "get_password",
        side_effect=get_password,
    ), patch.object(
        rp,
        "connect",
        side_effect=[
            rp.paramiko
            .AuthenticationException(),
            client,
        ],
    ) as mock_connect:

        result = (
            rp.open_old_session(
                device,
                "arista",
                bootstrap=True,
            )
        )

    assert result == (
        client,
        "Bootstrap123",
        "bootstrap",
    )

    assert (
        mock_connect.call_count
        == 2
    )


# ---------------------------------------------------------------------------
# Arista transaction helpers
# ---------------------------------------------------------------------------

def test_arista_change_password():

    device = (
        make_rotation_device()
    )

    client = MagicMock()

    channel = (
        client
        .invoke_shell
        .return_value
    )

    with patch.object(
        rp,
        "read_channel",
        return_value="",
    ), patch.object(
        rp,
        "send_command",
        return_value="OK",
    ) as mock_send:

        result = (
            rp.arista_change_password(
                device,
                client,
                "NewTest123",
            )
        )

    commands = [
        call.args[1]
        for call
        in mock_send.call_args_list
    ]

    assert commands == [
        "enable",
        "configure terminal",
        (
            "username admin "
            "secret 0 NewTest123"
        ),
        "end",
    ]

    assert (
        result
        is channel
    )


@pytest.mark.parametrize(
    "error",
    [
        "% Invalid input",
        "% Error",
        "Incomplete command",
        "Ambiguous command",
    ],
)
def test_arista_change_password_error(
    error,
):

    device = (
        make_rotation_device()
    )

    client = MagicMock()

    with patch.object(
        rp,
        "read_channel",
        return_value="",
    ), patch.object(
        rp,
        "send_command",
        return_value=error,
    ):

        with pytest.raises(
            RuntimeError,
            match=(
                "EOS returned "
                "a CLI error"
            ),
        ):

            rp.arista_change_password(
                device,
                client,
                "NewTest123",
            )


def test_arista_save():

    channel = MagicMock()

    with patch.object(
        rp,
        "send_command",
        return_value="OK",
    ) as mock_send:

        rp.arista_save(
            channel
        )

    mock_send.assert_called_once_with(
        channel,
        "write memory",
        delay=1.5,
    )


def test_arista_save_error():

    channel = MagicMock()

    with patch.object(
        rp,
        "send_command",
        return_value="% Error",
    ):

        with pytest.raises(
            RuntimeError,
            match=(
                "EOS write memory failed"
            ),
        ):

            rp.arista_save(
                channel
            )


def test_arista_rollback():

    device = (
        make_rotation_device()
    )

    channel = MagicMock()

    with patch.object(
        rp,
        "send_command",
        return_value="OK",
    ) as mock_send:

        rp.arista_rollback(
            device,
            channel,
            "OldTest123",
        )

    commands = [
        call.args[1]
        for call
        in mock_send.call_args_list
    ]

    assert commands == [
        "configure terminal",
        (
            "username admin "
            "secret 0 OldTest123"
        ),
        "end",
        "write memory",
    ]


# ---------------------------------------------------------------------------
# Nokia transaction helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "username,expected_command",
    [
        (
            "admin",
            (
                "system aaa authentication "
                "admin-user password "
                "NewTest123"
            ),
        ),
        (
            "student",
            (
                "system aaa authentication "
                "user student password "
                "NewTest123"
            ),
        ),
    ],
)
def test_srlinux_change_password(
    username,
    expected_command,
):

    device = (
        make_rotation_device(
            manufacturer="nokia",
            username=username,
        )
    )

    client = MagicMock()

    channel = (
        client
        .invoke_shell
        .return_value
    )

    with patch.object(
        rp,
        "read_channel",
        return_value="",
    ), patch.object(
        rp,
        "send_command",
        return_value="OK",
    ) as mock_send:

        result = (
            rp.srlinux_change_password(
                device,
                client,
                "NewTest123",
            )
        )

    commands = [
        call.args[1]
        for call
        in mock_send.call_args_list
    ]

    assert commands == [
        "enter candidate",
        expected_command,
        "commit now",
    ]

    assert (
        result
        is channel
    )


@pytest.mark.parametrize(
    "error",
    [
        "Error: invalid command",
        "Invalid command",
        "failed",
        "Failed to commit",
    ],
)
def test_srlinux_change_password_error(
    error,
):

    device = (
        make_rotation_device(
            manufacturer="nokia"
        )
    )

    client = MagicMock()

    with patch.object(
        rp,
        "read_channel",
        return_value="",
    ), patch.object(
        rp,
        "send_command",
        return_value=error,
    ):

        with pytest.raises(
            RuntimeError,
            match=(
                "SR Linux returned "
                "a CLI error"
            ),
        ):

            rp.srlinux_change_password(
                device,
                client,
                "NewTest123",
            )


def test_srlinux_rollback():

    device = (
        make_rotation_device(
            manufacturer="nokia"
        )
    )

    channel = MagicMock()

    with patch.object(
        rp,
        "send_command",
        return_value="OK",
    ) as mock_send:

        rp.srlinux_rollback(
            device,
            channel,
            "OldTest123",
        )

    commands = [
        call.args[1]
        for call
        in mock_send.call_args_list
    ]

    assert commands == [
        "enter candidate",
        (
            "system aaa authentication "
            "admin-user password "
            "OldTest123"
        ),
        "commit now",
    ]


# ---------------------------------------------------------------------------
# Password verification
# ---------------------------------------------------------------------------

def test_verify_password_success():

    device = (
        make_rotation_device()
    )

    client = MagicMock()

    with patch.object(
        rp,
        "connect",
        return_value=client,
    ) as mock_connect:

        result = (
            rp.verify_password(
                device,
                "NewTest123",
            )
        )

    mock_connect.assert_called_once_with(
        "192.0.2.1",
        "admin",
        "NewTest123",
    )

    assert (
        result
        is client
    )


def test_verify_password_failure():

    device = (
        make_rotation_device()
    )

    with patch.object(
        rp,
        "connect",
        side_effect=(
            rp.paramiko
            .AuthenticationException()
        ),
    ):

        with pytest.raises(
            rp.paramiko
            .AuthenticationException
        ):

            rp.verify_password(
                device,
                "WrongTest123",
            )


# ---------------------------------------------------------------------------
# rotate_one dry run
# ---------------------------------------------------------------------------

def test_rotate_one_dry_run():

    device = (
        make_rotation_device()
    )

    with patch.object(
        rp,
        "get_device",
        return_value=device,
    ), patch.object(
        rp,
        "open_old_session",
    ) as mock_session, patch.object(
        rp,
        "write_rotation_log",
    ) as mock_log:

        result = (
            rp.rotate_one(
                "R1",
                apply=False,
            )
        )

    assert result is True

    mock_session \
        .assert_not_called()

    mock_log \
        .assert_not_called()


# ---------------------------------------------------------------------------
# rotate_one Arista success
# ---------------------------------------------------------------------------

def test_rotate_one_arista_success():

    device = (
        make_rotation_device()
    )

    old_client = MagicMock()
    old_channel = MagicMock()
    new_client = MagicMock()

    with patch.object(
        rp,
        "get_device",
        return_value=device,
    ), patch.object(
        rp,
        "open_old_session",
        return_value=(
            old_client,
            "OldTest123",
            "secret-store",
        ),
    ), patch.object(
        rp,
        "generate_password",
        return_value="NewTest123",
    ), patch.object(
        rp,
        "arista_change_password",
        return_value=old_channel,
    ) as mock_change, patch.object(
        rp,
        "verify_password",
        return_value=new_client,
    ) as mock_verify, patch.object(
        rp,
        "arista_save",
    ) as mock_save, patch.object(
        rp,
        "update_secret",
    ) as mock_update, patch.object(
        rp,
        "write_rotation_log",
    ) as mock_log:

        result = (
            rp.rotate_one(
                "R1",
                apply=True,
            )
        )

    assert result is True

    mock_change \
        .assert_called_once_with(
            device,
            old_client,
            "NewTest123",
        )

    mock_verify \
        .assert_called_once_with(
            device,
            "NewTest123",
        )

    mock_save \
        .assert_called_once_with(
            old_channel
        )

    mock_update \
        .assert_called_once_with(
            "test-secret",
            "NewTest123",
        )

    mock_log \
        .assert_called_once_with(
            "R1",
            "success",
            "source=secret-store",
        )

    old_client.close \
        .assert_called_once()

    new_client.close \
        .assert_called_once()


# ---------------------------------------------------------------------------
# rotate_one Nokia success
# ---------------------------------------------------------------------------

def test_rotate_one_nokia_success():

    device = (
        make_rotation_device(
            manufacturer="nokia"
        )
    )

    old_client = MagicMock()
    old_channel = MagicMock()
    new_client = MagicMock()

    with patch.object(
        rp,
        "get_device",
        return_value=device,
    ), patch.object(
        rp,
        "open_old_session",
        return_value=(
            old_client,
            "OldTest123",
            "secret-store",
        ),
    ), patch.object(
        rp,
        "generate_password",
        return_value="NewTest123",
    ), patch.object(
        rp,
        "srlinux_change_password",
        return_value=old_channel,
    ) as mock_change, patch.object(
        rp,
        "verify_password",
        return_value=new_client,
    ) as mock_verify, patch.object(
        rp,
        "update_secret",
    ) as mock_update, patch.object(
        rp,
        "write_rotation_log",
    ) as mock_log:

        result = (
            rp.rotate_one(
                "R1",
                apply=True,
            )
        )

    assert result is True

    mock_change \
        .assert_called_once_with(
            device,
            old_client,
            "NewTest123",
        )

    mock_verify \
        .assert_called_once_with(
            device,
            "NewTest123",
        )

    mock_update \
        .assert_called_once_with(
            "test-secret",
            "NewTest123",
        )

    mock_log \
        .assert_called_once_with(
            "R1",
            "success",
            "source=secret-store",
        )

    old_client.close \
        .assert_called_once()

    new_client.close \
        .assert_called_once()


# ---------------------------------------------------------------------------
# rotate_one failure + rollback
# ---------------------------------------------------------------------------

def test_rotate_one_verification_failure_rolls_back():

    device = (
        make_rotation_device()
    )

    old_client = MagicMock()
    old_channel = MagicMock()
    rollback_verify = MagicMock()

    with patch.object(
        rp,
        "get_device",
        return_value=device,
    ), patch.object(
        rp,
        "open_old_session",
        return_value=(
            old_client,
            "OldTest123",
            "secret-store",
        ),
    ), patch.object(
        rp,
        "generate_password",
        return_value="NewTest123",
    ), patch.object(
        rp,
        "arista_change_password",
        return_value=old_channel,
    ), patch.object(
        rp,
        "verify_password",
        side_effect=[
            rp.paramiko
            .AuthenticationException(),
            rollback_verify,
        ],
    ) as mock_verify, patch.object(
        rp,
        "arista_rollback",
    ) as mock_rollback, patch.object(
        rp,
        "update_secret",
    ) as mock_update, patch.object(
        rp,
        "write_rotation_log",
    ) as mock_log:

        with pytest.raises(
            rp.paramiko
            .AuthenticationException
        ):

            rp.rotate_one(
                "R1",
                apply=True,
            )

    mock_rollback \
        .assert_called_once_with(
            device,
            old_channel,
            "OldTest123",
        )

    assert (
        mock_verify.call_count
        == 2
    )

    rollback_verify.close \
        .assert_called_once()

    mock_update \
        .assert_not_called()

    mock_log \
        .assert_called_once()

    assert (
        mock_log
        .call_args
        .args[1]
        == "failed"
    )

    old_client.close \
        .assert_called_once()


def test_rotate_one_change_failure_no_rollback():

    device = (
        make_rotation_device()
    )

    old_client = MagicMock()

    with patch.object(
        rp,
        "get_device",
        return_value=device,
    ), patch.object(
        rp,
        "open_old_session",
        return_value=(
            old_client,
            "OldTest123",
            "secret-store",
        ),
    ), patch.object(
        rp,
        "generate_password",
        return_value="NewTest123",
    ), patch.object(
        rp,
        "arista_change_password",
        side_effect=RuntimeError(
            "Password change failed"
        ),
    ), patch.object(
        rp,
        "arista_rollback",
    ) as mock_rollback, patch.object(
        rp,
        "verify_password",
    ) as mock_verify, patch.object(
        rp,
        "update_secret",
    ) as mock_update, patch.object(
        rp,
        "write_rotation_log",
    ) as mock_log:

        with pytest.raises(
            RuntimeError,
            match="Password change failed",
        ):

            rp.rotate_one(
                "R1",
                apply=True,
            )

    mock_rollback \
        .assert_not_called()

    mock_verify \
        .assert_not_called()

    mock_update \
        .assert_not_called()

    mock_log \
        .assert_called_once()

    assert (
        mock_log
        .call_args
        .args[1]
        == "failed"
    )

    old_client.close \
        .assert_called_once()


# ---------------------------------------------------------------------------
# Secret store
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_secret_store(
    tmp_path,
    monkeypatch,
):

    secrets_file = (
        tmp_path
        / "nmas-secrets"
    )

    monkeypatch.setattr(
        rp,
        "SECRETS_FILE",
        secrets_file,
    )

    real_mkstemp = (
        tempfile.mkstemp
    )

    def safe_mkstemp(
        *args,
        **kwargs,
    ):

        kwargs["dir"] = (
            str(
                tmp_path
            )
        )

        return real_mkstemp(
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        rp.tempfile,
        "mkstemp",
        safe_mkstemp,
    )

    return secrets_file


def test_update_secret_existing(
    isolated_secret_store,
):

    store = (
        isolated_secret_store
    )

    original = (
        "R1_PASSWORD='OldPass'\n"
        "R3_PASSWORD='OtherPass'\n"
    )

    store.write_text(
        original
    )

    rp.update_secret(
        "R1_PASSWORD",
        "NewPass",
    )

    assert (
        "R1_PASSWORD='NewPass'"
        in store.read_text()
    )

    assert (
        "R3_PASSWORD='OtherPass'"
        in store.read_text()
    )

    assert (
        store
        .with_suffix(".bak")
        .read_text()
        == original
    )

    assert (
        store.stat().st_mode
        & 0o777
        == 0o600
    )


def test_update_secret_new_key(
    isolated_secret_store,
):

    store = (
        isolated_secret_store
    )

    store.write_text(
        "R1_PASSWORD='OldPass'\n"
    )

    rp.update_secret(
        "R3_PASSWORD",
        "NewPass",
    )

    content = (
        store.read_text()
    )

    assert (
        "R1_PASSWORD='OldPass'"
        in content
    )

    assert (
        "R3_PASSWORD='NewPass'"
        in content
    )


def test_update_secret_missing_file(
    isolated_secret_store,
):

    store = (
        isolated_secret_store
    )

    with pytest.raises(
        RuntimeError,
        match="does not exist",
    ):

        rp.update_secret(
            "R1_PASSWORD",
            "NewPass",
        )

    assert (
        not store.exists()
    )

    assert (
        not store
        .with_suffix(".bak")
        .exists()
    )


def test_update_secret_replace_failure(
    isolated_secret_store,
):

    store = (
        isolated_secret_store
    )

    original = (
        "R1_PASSWORD='OldPass'\n"
    )

    store.write_text(
        original
    )

    with patch.object(
        rp.os,
        "replace",
        side_effect=OSError(
            "Simulated replace failure"
        ),
    ):

        with pytest.raises(
            OSError,
            match="Simulated",
        ):

            rp.update_secret(
                "R1_PASSWORD",
                "NewPass",
            )

    assert (
        store.read_text()
        == original
    )

    assert (
        store
        .with_suffix(".bak")
        .read_text()
        == original
    )


# ---------------------------------------------------------------------------
# Device enumeration
# ---------------------------------------------------------------------------

def test_get_all_device_names():

    data = {
        "results": [
            {
                "name": "R3",
                "device_type": {
                    "manufacturer": {
                        "name": "Nokia"
                    }
                },
            },
            {
                "name": "R1",
                "device_type": {
                    "manufacturer": {
                        "name": "Arista"
                    }
                },
            },
            {
                "name": "FW1",
                "device_type": {
                    "manufacturer": {
                        "name": "Cisco"
                    }
                },
            },
            {
                "name": "R2",
                "device_type": {
                    "manufacturer": {
                        "name": "ARISTA"
                    }
                },
            },
            {
                "name": "",
                "device_type": {
                    "manufacturer": {
                        "name": "Nokia"
                    }
                },
            },
            {
                "name": "Unknown",
                "device_type": {},
            },
        ]
    }

    with patch.object(
        rp,
        "netbox_get",
        return_value=data,
    ) as mock_get:

        result = (
            rp.get_all_device_names()
        )

    assert result == [
        "R1",
        "R2",
        "R3",
    ]

    mock_get.assert_called_once_with(
        "/api/dcim/devices/?limit=1000"
    )


@pytest.mark.parametrize(
    "data",
    [
        {
            "results": []
        },
        {},
    ],
)
def test_get_all_device_names_empty(
    data,
):

    with patch.object(
        rp,
        "netbox_get",
        return_value=data,
    ):

        assert (
            rp.get_all_device_names()
            == []
        )


def test_get_all_device_names_missing_fields():

    data = {
        "results": [
            {
                "name": "R1"
            },
            {
                "device_type": {
                    "manufacturer": {
                        "name": "Arista"
                    }
                }
            },
            {
                "name": "R3",
                "device_type": None,
            },
            {
                "name": "R4",
                "device_type": {
                    "manufacturer":
                        None
                },
            },
            {
                "name": "R5",
                "device_type": {
                    "manufacturer": {
                        "name": "Nokia"
                    }
                },
            },
        ]
    }

    with patch.object(
        rp,
        "netbox_get",
        return_value=data,
    ):

        result = (
            rp.get_all_device_names()
        )

    assert result == [
        "R5"
    ]