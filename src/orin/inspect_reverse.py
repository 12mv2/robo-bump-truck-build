# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Read-only XL-5/ArduRover configuration receipt. Never sends actuator commands.

Run on the Orin while the live tool is inhibited. Uses a distinct source system
and only PARAM_REQUEST_READ and AUTOPILOT_VERSION requests. No PARAM_SET, arm,
mode change, override, motor test, calibration or RC stream modification.
"""
import argparse
import datetime
import json
import time


PARAMETERS = (
    "RCMAP_THROTTLE", "RC2_MIN", "RC2_TRIM", "RC2_MAX", "RC2_DZ", "RC2_REVERSED",
    "SERVO3_FUNCTION", "SERVO3_MIN", "SERVO3_TRIM", "SERVO3_MAX", "SERVO3_REVERSED",
    "MOT_PWM_TYPE", "MOT_SLEWRATE", "MOT_REV_DELAY", "MOT_THR_MIN", "MOT_THR_MAX",
    "MOT_THST_EXPO", "MOT_SAFE_DISARM", "PILOT_STEER_TYPE", "MANUAL_OPTIONS",
    "RC_OVERRIDE_TIME", "RC_OPTIONS", "RC_FS_TIMEOUT", "FS_THR_ENABLE", "FS_THR_VALUE",
    "RC5_OPTION", "MAV_GCS_SYSID", "SYSID_MYGCS",
)


def wait_rover_heartbeat(connection, timeout=10):
    """Bind to the heartbeat sender, not pymavlink's default component zero."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hb = connection.recv_match(type="HEARTBEAT", blocking=True, timeout=.25)
        if hb is not None and hb.type == 10 and hb.autopilot == 3:  # Rover / ArduPilot
            connection.target_system = hb.get_srcSystem()
            connection.target_component = hb.get_srcComponent()
            return hb
    return None


def inspect(port):
    from pymavlink import mavutil
    m = mavutil.mavlink_connection(port, baud=115200, source_system=253)
    try:
        hb = wait_rover_heartbeat(m)
        if hb is None:
            raise RuntimeError("no FC heartbeat")
        params, version = {}, None
        for attempt in range(2):
            for name in PARAMETERS:
                if name not in params:
                    m.mav.param_request_read_send(m.target_system, m.target_component, name.encode(), -1)
            m.mav.command_long_send(m.target_system, m.target_component,
                                   mavutil.mavlink.MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES,
                                   0, 1, 0, 0, 0, 0, 0, 0)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                r = m.recv_match(blocking=True, timeout=.25)
                if r is None or r.get_srcSystem() != m.target_system:
                    continue
                if r.get_type() == "PARAM_VALUE":
                    name = r.param_id.decode() if isinstance(r.param_id, bytes) else r.param_id
                    name = name.rstrip("\x00")
                    if name in PARAMETERS:
                        params[name] = r.param_value
                elif r.get_type() == "AUTOPILOT_VERSION":
                    version = r.to_dict()
        return dict(read_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    port=port, parameters=params, missing=sorted(set(PARAMETERS) - params.keys()),
                    target_system=m.target_system, target_component=m.target_component,
                    heartbeat_source=[hb.get_srcSystem(), hb.get_srcComponent()],
                    heartbeat=hb.to_dict(), version=version, actuator_commands_sent=0,
                    parameter_writes=0)
    finally:
        m.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", required=True)
    a = ap.parse_args()
    print(json.dumps(inspect(a.port), indent=2))
