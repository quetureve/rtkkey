"""Data update coordinator for RTKkey."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import async_timeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import API_URL_DEVICES, API_URL_EVENTS, EVENT_TYPES, DEVICE_TYPE_INTERCOM, DEVICE_TYPE_GATE, DEFAULT_UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class RTKkeyDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching RTKkey data."""

    def __init__(self, hass: HomeAssistant, bearer_token: str, update_interval: int = DEFAULT_UPDATE_INTERVAL) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="RTKkey",
            update_interval=timedelta(minutes=update_interval),
        )
        self.bearer_token = bearer_token
        self.devices = []
        self._update_interval = update_interval

    async def _async_update_data(self):
        """Fetch data from API."""
        headers = {"Authorization": f"Bearer {self.bearer_token}"}
        
        _LOGGER.debug("Starting data update for RTKkey")
        
        try:
            # Get devices
            devices = await self._fetch_devices(headers)
            self.devices = devices
            
            # Get events for all devices
            events = await self._fetch_events(headers, devices)
            
            result = {
                "devices": devices,
                "events": events
            }
            
            _LOGGER.debug("Coordinator update completed. Devices: %d, Events for devices: %d", 
                         len(devices), len(events))
                
            return result
                
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        except asyncio.TimeoutError:
            raise UpdateFailed("Timeout communicating with API")
        except Exception as err:
            _LOGGER.exception("Unexpected error in coordinator")
            raise UpdateFailed(f"Unexpected error: {err}") from err

    async def _fetch_devices(self, headers):
        """Fetch devices from API."""
        async with async_timeout.timeout(10):
            async with aiohttp.ClientSession() as session:
                async with session.get(API_URL_DEVICES, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        _LOGGER.debug("Raw devices response: %s", data)
                        return self._parse_devices(data)
                    elif response.status == 401:
                        raise UpdateFailed("Invalid authentication")
                    else:
                        text = await response.text()
                        _LOGGER.error("API error response: %s", text)
                        raise UpdateFailed(f"API error: {response.status}")

    async def _fetch_events(self, headers, devices):
        """Fetch events from API."""
        if not devices:
            _LOGGER.debug("No devices to fetch events for")
            return {}
            
        # Get device IDs
        device_ids = []
        for device in devices:
            device_id = device.get('id')
            if device_id and device.get('device_type') in [DEVICE_TYPE_INTERCOM, DEVICE_TYPE_GATE]:
                device_ids.append(str(device_id))
                
        if not device_ids:
            _LOGGER.debug("No valid device IDs found for events")
            return {}
            
        _LOGGER.debug("Fetching events for device IDs: %s", device_ids)
        
        # Prepare time range - last 7 дней чтобы получить недавние события
        end_time = dt_util.utcnow()
        start_time = end_time - timedelta(days=7)
        
        params = {
            "begin_raised_at": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_raised_at": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "device_ids": ",".join(device_ids),
            "event_types": ",".join(EVENT_TYPES),
            "sort_by": "raised_at",
            "sort_order": "desc",  # Сначала самые новые события
            "offset": 0,
            "limit": 100  # API требует максимум 100
        }
        
        _LOGGER.debug("Events API params: %s", {k: v for k, v in params.items() if k != 'device_ids'})
        _LOGGER.debug("Device IDs for events: %s", device_ids)
        
        try:
            async with async_timeout.timeout(15):
                async with aiohttp.ClientSession() as session:
                    async with session.get(API_URL_EVENTS, headers=headers, params=params) as response:
                        if response.status == 200:
                            data = await response.json()
                            _LOGGER.debug("Successfully fetched events data")
                            parsed_events = self._parse_events(data)
                            _LOGGER.debug("Parsed events for %d devices", len(parsed_events))
                            return parsed_events
                        elif response.status == 400:
                            # Handle validation errors gracefully
                            error_data = await response.json()
                            _LOGGER.error("API validation error for events: %s", error_data)
                            # Return empty events but don't fail completely
                            return {}
                        else:
                            text = await response.text()
                            _LOGGER.warning("Failed to fetch events: %s - %s", response.status, text)
                            return {}
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout fetching events")
            return {}
        except Exception as err:
            _LOGGER.warning("Error fetching events: %s", err)
            return {}

    def _parse_devices(self, data):
        """Parse devices from API response."""
        if not isinstance(data, dict):
            raise UpdateFailed(f"Expected dictionary response, got {type(data)}")
        
        _LOGGER.debug("Parsing devices from response with keys: %s", list(data.keys()))
        
        # Try different response structures
        devices = []
        
        # Structure 1: data -> data -> devices
        if "data" in data and isinstance(data["data"], dict):
            data_content = data["data"]
            if "devices" in data_content and isinstance(data_content["devices"], list):
                devices = data_content["devices"]
        
        # Structure 2: data -> devices
        elif "devices" in data and isinstance(data["devices"], list):
            devices = data["devices"]
        
        # Structure 3: direct devices list
        elif isinstance(data.get("data"), list):
            devices = data["data"]
        
        # Filter only intercom and gate devices
        valid_devices = [
            device for device in devices 
            if isinstance(device, dict) and device.get('device_type') in [DEVICE_TYPE_INTERCOM, DEVICE_TYPE_GATE]
        ]
        
        _LOGGER.info("Found %d valid devices (intercom/gate)", len(valid_devices))
        
        for device in valid_devices:
            device_id = device.get('id')
            device_name = device.get('description') or device.get('name_by_user') or device.get('name_by_company')
            _LOGGER.debug("Device: ID=%s, Name=%s, Type=%s", device_id, device_name, device.get('device_type'))
            
        if not valid_devices:
            _LOGGER.warning("No valid devices found in response")
            
        return valid_devices

    def _parse_events(self, data):
        """Parse events from API response."""
        if not isinstance(data, dict):
            _LOGGER.warning("Expected dictionary for events, got %s", type(data))
            return {}
            
        events_by_device = {}
        
        try:
            # Based on the response example, events are in data -> items
            events_list = []
            
            if "data" in data and isinstance(data["data"], dict):
                if "items" in data["data"] and isinstance(data["data"]["items"], list):
                    events_list = data["data"]["items"]
                    _LOGGER.debug("Found events in data->items: %d events", len(events_list))
            
            # If no events found, try alternative structures
            if not events_list:
                # Try other possible structures
                if "items" in data and isinstance(data["items"], list):
                    events_list = data["items"]
                elif "events" in data and isinstance(data["events"], list):
                    events_list = data["events"]
                elif "data" in data and isinstance(data["data"], list):
                    events_list = data["data"]
                    
            _LOGGER.debug("Total events found in response: %d", len(events_list))
            
            # Group events by device_id (as string for consistency)
            event_count = 0
            for event in events_list:
                if isinstance(event, dict):
                    device_id = event.get("device_id")
                    if device_id:
                        device_id_str = str(device_id)
                        if device_id_str not in events_by_device:
                            events_by_device[device_id_str] = []
                        events_by_device[device_id_str].append(event)
                        event_count += 1
                        
            _LOGGER.debug("Successfully parsed %d events for %d devices", event_count, len(events_by_device))
            
            # Log event count per device
            for device_id, events in events_by_device.items():
                _LOGGER.debug("Device %s has %d events", device_id, len(events))
                if events:
                    # Sort events by raised_at to find the latest (descending order)
                    sorted_events = sorted(
                        events, 
                        key=lambda x: self._parse_event_time(x.get("raised_at")), 
                        reverse=True
                    )
                    latest_event = sorted_events[0]
                    _LOGGER.debug("Latest event for device %s: %s - %s", 
                                 device_id, latest_event.get("raised_at"), latest_event.get("event_type"))
                        
        except Exception as err:
            _LOGGER.error("Error parsing events: %s. Data: %s", err, data)
            
        return events_by_device

    def _parse_event_time(self, date_string):
        """Parse event time for sorting."""
        if not date_string:
            return datetime.min
        try:
            parsed = dt_util.parse_datetime(date_string)
            if parsed:
                return parsed.replace(tzinfo=dt_util.UTC)
            return datetime.min
        except:
            return datetime.min