"""通过 MQTT 发现（Discovery）把国网电量/电费传感器归入同一个 Home Assistant 设备。

一个户号 = 一个 HA 设备（device），该户号的全部传感器实体都挂在这个设备下，
在「设置 → 设备与服务」中可以看到统一的「国网电费」设备卡片。

未配置 MQTT_HOST 时不启用，SensorUpdator 会回退到 Home Assistant REST API。

所需环境变量：
    MQTT_HOST                MQTT 服务器地址（留空则不启用）
    MQTT_PORT                默认 1883
    MQTT_USERNAME / MQTT_PASSWORD  可选
    MQTT_DISCOVERY_PREFIX    默认 homeassistant，需与 HA 的 MQTT 发现前缀一致
"""

import json
import logging
import os

try:
    import paho.mqtt.client as mqtt
    _HAS_PAHO = True
except ImportError:
    mqtt = None
    _HAS_PAHO = False

from const import (DEVICE_MANUFACTURER, DEVICE_MODEL, DEVICE_NAME,
                   DEVICE_SUPPORT_URL, sensor_postfix)


class MqttDevice:
    """一个户号对应一个 Home Assistant 设备。"""

    def __init__(self, user_id: str):
        self.user_id = str(user_id)
        self.node_id = f"sgcc_{self.user_id}"
        self.base_topic = f"sgcc_electricity/{self.user_id}"
        postfix = sensor_postfix(self.user_id)
        self.device_name = (f"{DEVICE_NAME} {postfix.lstrip('_')}" if postfix else DEVICE_NAME)
        self.discovery_prefix = (
            os.getenv("MQTT_DISCOVERY_PREFIX", "homeassistant").strip().strip("/")
            or "homeassistant")
        self._configs = set()
        self._client = None

    @staticmethod
    def enabled() -> bool:
        """是否启用 MQTT 设备模式：配置了 MQTT_HOST 即启用。"""
        return bool(os.getenv("MQTT_HOST", "").strip())

    # ── 连接 ──

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not _HAS_PAHO:
            raise RuntimeError("未安装 paho-mqtt，请执行 pip install paho-mqtt")
        host = os.getenv("MQTT_HOST").strip()
        port = int(os.getenv("MQTT_PORT", 1883))
        client_id = f"sgcc_electricity_{self.user_id}"
        try:    # paho-mqtt 2.x 要求显式指定回调版本
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        except AttributeError:
            client = mqtt.Client(client_id=client_id)
        username = os.getenv("MQTT_USERNAME", "").strip()
        if username:
            client.username_pw_set(username, os.getenv("MQTT_PASSWORD", ""))
        client.connect(host, port, 60)
        client.loop_start()
        self._client = client
        logging.info(f"[mqtt] 已连接 {host}:{port}")
        return client

    def _publish_raw(self, topic: str, payload: str, retain: bool = True):
        info = self._ensure_client().publish(topic, payload, qos=1, retain=retain)
        try:
            info.wait_for_publish(timeout=5)
        except Exception:
            pass

    # ── 对外接口 ──

    def publish(self, sensor_name: str, state, attributes: dict) -> bool:
        """发布发现配置（仅首次）+ 状态与属性，成功返回 True。"""
        try:
            object_id = sensor_name.split(".", 1)[-1]
            self._publish_config(object_id, sensor_name, attributes)
            topic = f"{self.base_topic}/{object_id}"
            self._publish_raw(f"{topic}/state", str(state))
            self._publish_raw(f"{topic}/attributes", json.dumps(attributes, ensure_ascii=False))
            logging.debug(f"[mqtt] {sensor_name} 已发布: {state}")
            return True
        except Exception as e:
            logging.error(f"[mqtt] {sensor_name} 发布失败: {e}")
            return False

    def _publish_config(self, object_id: str, sensor_name: str, attributes: dict):
        if object_id in self._configs:
            return
        version = os.getenv("VERSION", "")
        config = {
            # 保留原有 entity_id（HA 2023.8+ 支持 default_entity_id）
            "default_entity_id": sensor_name,
            "name": attributes.get("friendly_name") or sensor_name,
            "unique_id": f"{self.node_id}_{object_id}",
            "state_topic": f"{self.base_topic}/{object_id}/state",
            "json_attributes_topic": f"{self.base_topic}/{object_id}/attributes",
            "device": {
                "identifiers": [self.node_id],
                "name": self.device_name,
                "manufacturer": DEVICE_MANUFACTURER,
                "model": DEVICE_MODEL,
                "sw_version": version,
                "configuration_url": DEVICE_SUPPORT_URL,
            },
            "origin": {
                "name": "sgcc_electricity_new",
                "sw_version": version,
                "support_url": DEVICE_SUPPORT_URL,
            },
        }
        for key in ("unit_of_measurement", "device_class", "state_class", "icon"):
            if attributes.get(key):
                config[key] = attributes[key]
        topic = f"{self.discovery_prefix}/sensor/{self.node_id}/{object_id}/config"
        self._publish_raw(topic, json.dumps(config, ensure_ascii=False))
        self._configs.add(object_id)
        logging.info(f"[mqtt] 设备[{self.device_name}] 已注册实体 {sensor_name}")

    def close(self):
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
                logging.info("[mqtt] 已断开")
            except Exception:
                pass
            self._client = None
