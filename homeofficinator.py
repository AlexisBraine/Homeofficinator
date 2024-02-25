import datetime
import re
from collections import OrderedDict
from dataclasses import dataclass
from functools import cached_property
from typing import Iterator, Any, Callable
import streamlit as st
import requests

from dateutil import rrule

# region Constants
HOST = "https://lengow.ilucca.net"
POST_LEAVE_ROUTE = "/api/v3/leaveRequestFactory?isCreation=true"
GET_LEAVE_ROUTE = "/api/v3/leaves"

HTTP_DEBUG = True

PAYLOAD = {
    "daysUnit": True,
    "displayAllUnits": False,
    "warnings": [],
    "agreementWarnings": [],
    "balanceEstimateEndsOn": "2023-04-30T00:00:00",
    "availableAccounts": [],
    "otherAvailableAccounts": [
        {
            "leaveAccountId": 32,
            "leaveAccountName": "_Télétravail",
            "unit": 0,
            "duration": 1,
            "isRemoteWork": True,
            "constraint": {
                "allowOuterConsumption": 0,
                "durationHour": 0,
                "stepHour": 0.5,
                "entitlementEndDateBalance": None,
                "warnings": [],
            },
        }
    ],
    "daysOff": {},
    "unlimitedDaysOffCalculation": False,
    "isValid": True,
    "areSupportingDocumentsManaged": True,
    "withCandidate": False,
    "startsAM": True,
    "endsAM": False,
    "isHalfDay": False,
    "unit": 0,
    "autoCreate": True,
}

DAYS = OrderedDict(
    [
        ("Monday", rrule.MO),
        ("Tuesday", rrule.TU),
        ("Wednesday", rrule.WE),
        ("Thursday", rrule.TH),
        ("Friday", rrule.FR),
    ]
)
# endregion


# region helpers
@dataclass
class Params:
    cookies: str
    date_from: datetime.datetime
    date_to: datetime.datetime
    owner_id: int
    days: list[str]

    @cached_property
    def rrule_days(self) -> list[rrule.weekday]:
        return [DAYS[d] for d in self.days]

    @cached_property
    def session(self) -> requests.Session:
        sess = requests.Session()
        sess.cookies.update(
            dict(c.strip().rsplit("=", 1) for c in self.cookies.split(";"))
        )
        return sess

    @cached_property
    def owner_name(self) -> str:
        ...

    def close(self):
        self.session.close()

    def _check_cookies(self) -> bool:
        if not re.match(r".+=.+(;.+=.+)*;?", self.cookies):
            st.error("Cookies format is not ok")
        else:
            return True

    def _check_dates(self) -> bool:
        if (
            self.date_from > self.date_to
            or self.date_to - self.date_from > datetime.timedelta(days=100)
        ):
            st.error("Dates incorrect (cannot be more than 100 days apart")
        else:
            return True

    def _check_filled(self) -> bool:
        if not_filled := ", ".join(
            f for f in self.__annotations__ if not getattr(self, f)
        ):
            st.error(f"The fields {not_filled} need to be filled")
        else:
            return True

    def validate(self) -> bool:
        if self._check_filled() and self._check_cookies() and self._check_dates():
            return True


# endregion


# region http calls
def _http_get_all_leaves(
    sess: requests.Session, data: dict[str, Any]
) -> requests.Response:
    return sess.get(
        HOST + GET_LEAVE_ROUTE,
        params=data,
    )


def _http_request_leave(
    sess: requests.Session, pl: dict[str, Any]
) -> requests.Response:
    return sess.post(HOST + POST_LEAVE_ROUTE, json=pl)


# endregion


def main():
    """Main method, executed at every cycle"""
    st.write(
        """# HomeOfficinator
    *The ultimate solution for home office management with Lucca*
    """
    )
    p = Params(
        cookies=st.text_input("Cookies plz"),
        owner_id=st.text_input("User ID"),
        days=st.multiselect("Days of week", DAYS.keys()),
        date_from=st.date_input("From :", value=datetime.datetime.now()),
        date_to=st.date_input(
            "To :", value=datetime.datetime.now() + datetime.timedelta(days=60)
        ),
    )

    if st.button("Let's go") and p.validate():
        st.write_stream(order_home_office(p))
        p.close()


def order_home_office(params: Params) -> Iterator[str]:
    """Order all home office leaves for the given period, yields logs"""
    # Get all leaves
    leaves, owner_name = _get_all_leaves(params)
    # Request missing leaves
    for day in rrule.rrule(
        rrule.DAILY, byweekday=params.rrule_days, dtstart=params.date_from
    ).between(params.date_from, params.date_to, inc=True):
        yield f">>> Trying for {day}..."
        if day in leaves:
            yield "[x] Already taken"
            continue

        daystr = day.strftime("%Y-%m-%dT00:00:00")
        data = dict(
            PAYLOAD,
            startsOn=daystr,
            endsOn=daystr,
            ownerId=params.owner_id,
            ownerName=owner_name,
        )
        ans = _http_request_leave(params.session, data)
        if ans.status_code == 200:
            yield "[+] SUCCESS"
        else:
            yield f"[-] FAILURE : {ans.status_code}"


def _get_all_leaves(params: Params) -> tuple[set[datetime.datetime], str]:
    """Get all current leaves from Lucca for the current user (and also their name)"""
    data = {
        "leavePeriod.ownerId": params.owner_id,
        "date": f"between,{params.date_from:%Y-%m-%d},{params.date_to:%Y-%m-%d}",
    }
    ans = _http_get_all_leaves(params.session, data)
    owner_name = ans.json()["header"]["principal"]
    leaves = {
        datetime.datetime.strptime(leave["name"].split("-", 2)[1], "%Y%m%d")
        for leave in ans.json()["data"]["items"]
    }
    return leaves, owner_name


main()
