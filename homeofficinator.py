import datetime
import re
from dataclasses import dataclass
from functools import cached_property
from typing import Iterator, Any, Annotated
import requests
from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QPushButton,
    QLineEdit,
    QLabel,
    QSpinBox,
    QDateEdit,
    QCheckBox,
    QMainWindow,
    QMessageBox,
    QScrollArea,
)

from dateutil import rrule
from pydantic import (
    BaseModel,
    model_validator,
    BeforeValidator,
    PositiveInt,
    ValidationError,
)

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

# endregion


# region helpers
@dataclass
class DataError(Exception):
    msg: str

    def __str__(self):
        return self.msg


def wdate_to_datetime(wdate: Any) -> datetime.datetime:
    return datetime.datetime.combine(wdate.toPyDate(), datetime.time())


def check_cookies(raw_cookies: str) -> dict[str, str]:
    if not raw_cookies:
        raise DataError("Please provide cookies")
    if not re.match(r".+=.+(;.+=.+)*;?", raw_cookies):
        raise DataError("Cookies format is not ok")
    return dict(c.strip().rsplit("=", 1) for c in raw_cookies.split(";"))


def check_days(raw_days: list[tuple[rrule.weekday, QCheckBox]]) -> list[rrule.weekday]:
    res = [d[0] for d in raw_days if d[1].isChecked()]
    if not res:
        raise DataError("Select at least one day")
    return res


DateValidator = Annotated[datetime.datetime, BeforeValidator(wdate_to_datetime)]
Cookies = Annotated[dict[str, str], BeforeValidator(check_cookies)]
DayList = Annotated[list[rrule.weekday], BeforeValidator(check_days)]


class Params(BaseModel):

    class Config:
        arbitrary_types_allowed = True

    cookies: Cookies
    date_from: DateValidator
    date_to: DateValidator
    owner_id: PositiveInt
    days: DayList

    @model_validator(mode="after")
    def _check_dates(self) -> None:
        if (
            self.date_from > self.date_to
            or self.date_to - self.date_from > datetime.timedelta(days=100)
        ):
            raise DataError("Dates incorrect (cannot be more than 100 days apart")

    @cached_property
    def session(self) -> requests.Session:
        sess = requests.Session()
        sess.cookies.update(self.cookies)
        return sess

    @cached_property
    def owner_name(self) -> str:
        ...
        # TODO IMPLEMENT

    def close(self):
        self.session.close()


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
    sess: requests.Session, data: dict[str, Any]
) -> requests.Response:
    return sess.post(HOST + POST_LEAVE_ROUTE, json=data)


# endregion


class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()
        self._logs = []
        self.setWindowTitle("Homeofficinator")
        layout = QVBoxLayout()
        self._w_cookies = QLineEdit()
        self._w_owner_id = QSpinBox()
        self._w_days = [
            (rrule_day, QCheckBox(day_name))
            for (day_name, rrule_day) in [
                ("Monday", rrule.MO),
                ("Tuesday", rrule.TU),
                ("Wednesday", rrule.WE),
                ("Thursday", rrule.TH),
                ("Friday", rrule.FR),
            ]
        ]
        now = datetime.date.today()
        self._w_date_from = QDateEdit()
        self._w_date_from.setDisplayFormat("dd/MM/yyyy")
        self._w_date_from.setDate(now)
        self._w_date_to = QDateEdit()
        self._w_date_to.setDisplayFormat("dd/MM/yyyy")
        self._w_date_to.setDate(now + datetime.timedelta(days=60))
        self._w_button = QPushButton("Let's go !")
        self._w_button.clicked.connect(self.validate)
        self._w_logs = QLabel()
        self._w_logs.setWordWrap(True)
        scroll = QScrollArea()
        scroll.setWidget(self._w_logs)
        scroll.setWidgetResizable(True)
        for w in (
            [
                QLabel("Cookies"),
                self._w_cookies,
                QLabel("Owner ID"),
                self._w_owner_id,
                QLabel("Days"),
            ]
            + [d[1] for d in self._w_days]
            + [
                QLabel("Date from"),
                self._w_date_from,
                QLabel("Date to"),
                self._w_date_to,
                self._w_button,
                scroll,
            ]
        ):
            layout.addWidget(w)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def log(self, text: str):
        self._logs.append(text)
        self._w_logs.setText("\n".join(self._logs))

    def validate(self):
        try:
            p = Params(
                cookies=self._w_cookies.text(),
                owner_id=self._w_owner_id.text(),
                days=self._w_days,
                date_from=self._w_date_from.date(),
                date_to=self._w_date_to.date(),
            )
        except (DataError, ValidationError) as e:
            QMessageBox.critical(self, "Error", str(e))
        else:
            for log in self.order_home_office(p):
                self.log(log)
            p.close()

    def order_home_office(self, params: Params) -> Iterator[str]:
        """Order all home office leaves for the given period, yields logs"""
        # Get all leaves
        leaves, owner_name = self._get_all_leaves(params)
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
                ownerName=owner_name,
            )
            ans = _http_request_leave(params.session, data)
            if ans.status_code == 200:
                yield "[+] SUCCESS"
            else:
                yield f"[-] FAILURE : {ans.status_code}"

    @staticmethod
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


def main():
    """Main method, executed at every cycle"""
    app = QApplication([])
    window = MainWindow()
    window.show()
    try:
        app.exec()
    except Exception as e:
        print(e.__class__)


main()
