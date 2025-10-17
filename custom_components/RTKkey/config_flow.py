"""Config flow for RTKkey integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, API_URL_DEVICES, DEFAULT_UPDATE_INTERVAL, CONF_UPDATE_INTERVAL, CONF_BEARER_TOKEN

_LOGGER = logging.getLogger(__name__)

async def validate_auth(hass: HomeAssistant, bearer_token: str) -> bool:
    """Validate the bearer token by making a test API call."""
    headers = {"Authorization": f"Bearer {bearer_token}"}
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(API_URL_DEVICES, headers=headers) as response:
                if response.status == 200:
                    return True
                elif response.status == 401:
                    raise InvalidAuth
                else:
                    raise CannotConnect
        except aiohttp.ClientError:
            raise CannotConnect

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for RTKkey."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                await validate_auth(self.hass, user_input["bearer_token"])
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception during setup")
                errors["base"] = "unknown"
            else:
                # Set default update interval if not provided
                if CONF_UPDATE_INTERVAL not in user_input:
                    user_input[CONF_UPDATE_INTERVAL] = DEFAULT_UPDATE_INTERVAL
                    
                return self.async_create_entry(
                    title="RTKkey",
                    data=user_input,
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_BEARER_TOKEN): str,
                vol.Optional(
                    CONF_UPDATE_INTERVAL, 
                    default=DEFAULT_UPDATE_INTERVAL
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "update_interval": str(DEFAULT_UPDATE_INTERVAL)
            },
        )

    async def async_step_reauth(self, user_input=None):
        """Handle reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Handle reauthentication confirmation."""
        errors = {}

        if user_input is not None:
            try:
                await validate_auth(self.hass, user_input["bearer_token"])
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"
            else:
                existing_entry = await self.async_set_unique_id(self.unique_id)
                if existing_entry:
                    self.hass.config_entries.async_update_entry(
                        existing_entry, data=user_input
                    )
                    await self.hass.config_entries.async_reload(existing_entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_BEARER_TOKEN): str}),
            errors=errors,
        )

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""