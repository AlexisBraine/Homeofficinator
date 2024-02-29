import datetime
import re
import webbrowser
from abc import ABC
from dataclasses import dataclass
from functools import cached_property, wraps
from tkinter import (
    VERTICAL,
    HORIZONTAL,
    Frame,
    PanedWindow,
    StringVar,
    Entry,
    Button,
    Checkbutton,
    Label,
    Tk,
    IntVar,
    Toplevel,
    LEFT,
    Text,
    END,
)
from tkinter.messagebox import showerror
from typing import Iterator, Any, Annotated, Callable
import requests

from dateutil import rrule
from pydantic import (
    BaseModel,
    model_validator,
    BeforeValidator,
)
from tkcalendar import DateEntry

# Just for nuitka
from babel import numbers

_nope = numbers.overload


# region Constants
HOST = "https://lengow.ilucca.net"
POST_LEAVE_ROUTE = "/api/v3/leaveRequestFactory?isCreation=true"
GET_LEAVE_ROUTE = "/api/v3/leaves"
ME_ROUTE = "/api/v3/users/me"
MAX_DAYS_BETWEEN_DATES = 100

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


# endregion


# region helpers
@dataclass
class DataError(Exception):
    msg: str

    def __str__(self):
        return self.msg


class ExecutionError(Exception, ABC): ...


@dataclass
class HttpError(ExecutionError):
    route: str
    function: str
    err: Exception

    def __str__(self):
        return f"Network error on {self.route} ({self.function}) : {self.err}"


def date_to_datetime(date_: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(date_, datetime.time())


def check_auth_token(auth_token: str) -> str:
    if not auth_token:
        raise DataError("Please provide authToken")
    if not re.match(r"[0-9a-g-]+", auth_token):
        raise DataError("AuthToken format is not ok")
    return auth_token


def check_days(
    raw_days: list[tuple[rrule.weekday, Any, IntVar]]
) -> list[rrule.weekday]:
    res = [rrule_day for _, rrule_day, var in raw_days if var.get()]
    if not res:
        raise DataError("Select at least one day")
    return res


DateValidator = Annotated[datetime.datetime, BeforeValidator(date_to_datetime)]
AuthTokenCookie = Annotated[str, BeforeValidator(check_auth_token)]
DayList = Annotated[list[rrule.weekday], BeforeValidator(check_days)]


class Params(BaseModel):
    """Dataclass for all the user-selected data"""

    class Config:
        arbitrary_types_allowed = True

    auth_token: AuthTokenCookie
    date_from: DateValidator
    date_to: DateValidator
    days: DayList

    @model_validator(mode="after")
    def _check_dates(self) -> None:
        """Check the validity of the chosen dates, relative to each other"""
        if (
            self.date_from > self.date_to
            or self.date_to - self.date_from
            > datetime.timedelta(days=MAX_DAYS_BETWEEN_DATES)
        ):
            raise DataError("Dates are incorrect (cannot be more than 100 days apart)")

    @cached_property
    def session(self) -> requests.Session:
        """Return a session initiated with the user-given sweet sweet cookies"""
        sess = requests.Session()
        sess.cookies.update({"authToken": self.auth_token})
        return sess

    @cached_property
    def owner_id(self) -> int:
        """Using the session (thus the user's cookies), get the user ID"""
        ans = _http_get_owner_id(self.session)
        owner_id = ans.json()["data"]["id"]
        print(f"Owner ID is {owner_id}")
        return owner_id

    def close(self):
        """Close the requests session"""
        self.session.close()


# endregion


# region http calls
#  Here, all the HTTP calls will be stored, for ease of decoupling
def decorate_http(route: str):
    def wrap(
        func: Callable[..., requests.Response]
    ) -> Callable[..., requests.Response]:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                ans = func(*args, **kwargs)
                ans.raise_for_status()
                return ans
            except Exception as e:
                raise HttpError(route, func.__name__, e)

        return wrapper

    return wrap


@decorate_http(HOST + GET_LEAVE_ROUTE)
def _http_get_all_leaves(
    sess: requests.Session, data: dict[str, Any]
) -> requests.Response:
    """Get all current leaves for the user"""
    return sess.get(
        HOST + GET_LEAVE_ROUTE,
        params=data,
    )


@decorate_http(HOST + POST_LEAVE_ROUTE)
def _http_request_leave(
    sess: requests.Session, data: dict[str, Any]
) -> requests.Response:
    """Post a request for a specific leave"""
    return sess.post(HOST + POST_LEAVE_ROUTE, json=data)


@decorate_http(HOST + ME_ROUTE)
def _http_get_owner_id(sess: requests.Session) -> requests.Response:
    """Get the owner ID from their cookies"""
    return sess.get(HOST + ME_ROUTE, params={"fields": "id"})


# endregion


class CookiesDialog:

    COOKIES_EXPLANATION = (
        f"You can grab your cookies from {HOST}. To do so, "
        f"simply go on this URL, open the dev tools with F12, "
        f"and go on the Application tab.\n"
        f"Then, into the Storage section, select Cookies, then {HOST} "
        f"in the dropdown. It should show you an array, with multiple "
        f'columns like "Name" and "Value".\n'
        f'On the line name "authToken", copy the value and paste it '
        f"in this field"
    )

    def __init__(self, master):
        self.top = Toplevel(master)
        self.top.title("How to get my cookies ?")

        main_layout = PanedWindow(
            self.top,
            orient=VERTICAL,
        )
        main_layout.add(
            Label(
                main_layout, text=self.COOKIES_EXPLANATION, wraplength=400, justify=LEFT
            )
        )
        button_layout = PanedWindow(main_layout, orient=HORIZONTAL)
        button_layout.add(Button(button_layout, text="Close", command=self.top.destroy))
        button_layout.add(
            Button(
                button_layout,
                text="Open browser",
                command=self.button_handler,
            )
        )
        main_layout.add(button_layout)
        main_layout.pack()

    @staticmethod
    def button_handler():
        webbrowser.open(HOST)


class MainWindow(Frame):
    """Main Tk window"""

    def __init__(self, master: Toplevel | Tk):
        super(MainWindow, self).__init__(master)
        # I don't want the current window to be resizable
        master.resizable(False, False)

        main_paned_window = PanedWindow(self, orient=VERTICAL, width=300)

        master.title("Homeofficinator")

        # Cookies group
        cookies_group = PanedWindow(main_paned_window, orient=HORIZONTAL)
        self._var_authtoken = StringVar()
        cookies_group.add(Entry(cookies_group, textvariable=self._var_authtoken))
        cookies_group.add(
            Button(
                cookies_group,
                text="?",
                width=10,
                command=lambda: master.wait_window(
                    CookiesDialog(main_paned_window).top
                ),
            )
        )
        cookies_group.pack()

        self._w_days = [
            ("Monday", rrule.MO, IntVar()),
            ("Tuesday", rrule.TU, IntVar()),
            ("Wednesday", rrule.WE, IntVar()),
            ("Thursday", rrule.TH, IntVar()),
            ("Friday", rrule.FR, IntVar()),
        ]
        days_checkbuttons = [
            Checkbutton(main_paned_window, text=day_name, variable=var, anchor="w")
            for day_name, _, var in self._w_days
        ]

        now = datetime.date.today()
        self._widget_date_from = DateEntry(main_paned_window, locale="fr_FR")
        self._widget_date_from.set_date(now)
        self._widget_date_to = DateEntry(main_paned_window, locale="fr_FR")
        self._widget_date_to.set_date(now + datetime.timedelta(days=60))
        main_button = Button(
            main_paned_window, text="Let's go !", command=self.validate
        )
        self._var_logs = StringVar()
        self._widget_logs_text = Text(
            main_paned_window,
            background="black",
            foreground="lightgrey",
            font="Monospace 10",
            height=10,
        )
        for w in (
            [
                Label(main_paned_window, text="AuthToken", anchor="w"),
                cookies_group,
                Label(main_paned_window, text="Days", anchor="w"),
            ]
            + days_checkbuttons
            + [
                Label(main_paned_window, text="Date from", anchor="w"),
                self._widget_date_from,
                Label(main_paned_window, text="Date to", anchor="w"),
                self._widget_date_to,
                main_button,
                self._widget_logs_text,
            ]
        ):
            main_paned_window.add(w)
        main_paned_window.pack(side=LEFT)
        self.pack(side=LEFT)

    def log(self, text: str):
        """Logging-like mechanism to display real-time messages"""
        self._widget_logs_text.insert(END, text + "\n")

    def validate(self) -> None:
        """Validate and process the home office requests"""
        p = None
        try:
            p = Params(
                auth_token=self._var_authtoken.get(),
                days=self._w_days,
                date_from=self._widget_date_from.get_date(),
                date_to=self._widget_date_to.get_date(),
            )
            for log in self.order_home_office(p):
                self.log(log)
            p.close()
        except DataError as e:
            showerror("Error in the parameters", str(e))
        except ExecutionError as e:
            showerror("Error during execution", str(e))
        finally:
            if p:
                p.close()

    def order_home_office(self, params: Params) -> Iterator[str]:
        """Order all home office leaves for the given period, yields logs"""
        # Get all leaves
        leaves = self._get_all_leaves(params)
        # Request missing leaves
        for day in rrule.rrule(
            rrule.DAILY, byweekday=params.days, dtstart=params.date_from
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
            )
            ans = _http_request_leave(params.session, data)
            if ans.status_code == 200:
                yield "[+] SUCCESS"
            else:
                yield f"[-] FAILURE : {ans.status_code}"

    @staticmethod
    def _get_all_leaves(params: Params) -> set[datetime.datetime]:
        """Get all current leaves from Lucca for the current user"""
        data = {
            "leavePeriod.ownerId": params.owner_id,
            "date": f"between,{params.date_from:%Y-%m-%d},{params.date_to:%Y-%m-%d}",
        }
        ans = _http_get_all_leaves(params.session, data)
        leaves = {
            datetime.datetime.strptime(leave["name"].split("-", 2)[1], "%Y%m%d")
            for leave in ans.json()["data"]["items"]
        }
        return leaves


def main():
    """Main method, executed at every cycle"""
    root = Tk()
    app = MainWindow(root)
    app.mainloop()


main()
