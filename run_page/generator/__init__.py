import datetime
import os
import sys

import arrow
import stravalib
from gpxtrackposter import track_loader
from polyline_processor import filter_out
from sqlalchemy import func
from strava_rate_limit import wait_for_strava_quota
from strava_routes import resolve_activity_route
from stravalib.exc import ObjectNotFound
from synced_data_file_logger import save_synced_data_file_list

from .db import Activity, init_db, update_or_create_activity

IGNORE_BEFORE_SAVING = os.getenv(
    "IGNORE_BEFORE_SAVING",
    False,  # noqa: PLW1508
)


class Generator:
    def __init__(self, db_path):
        self.client = stravalib.Client(rate_limiter=wait_for_strava_quota)
        self.session = init_db(db_path)

        self.client_id = ""
        self.client_secret = ""
        self.refresh_token = ""
        self.only_run = False

    def set_strava_config(self, client_id, client_secret, refresh_token):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token

    def check_access(self):
        response = self.client.refresh_access_token(
            client_id=self.client_id,
            client_secret=self.client_secret,
            refresh_token=self.refresh_token,
        )
        # Update the authdata object
        self.access_token = response["access_token"]
        self.refresh_token = response["refresh_token"]

        self.client.access_token = response["access_token"]
        print("Access ok")

    def sync(self, force):
        """
        Sync activities means sync from strava
        TODO, better name later
        """
        self.check_access()

        print("Start syncing")
        if force:
            filters = {"before": datetime.datetime.now(datetime.UTC)}
        else:
            last_activity = self.session.query(func.max(Activity.start_date)).scalar()
            if last_activity:
                last_activity_date = arrow.get(last_activity)
                last_activity_date = last_activity_date.shift(days=-7)
                filters = {"after": last_activity_date.datetime}
            else:
                filters = {"before": datetime.datetime.now(datetime.UTC)}

        synced_count = 0
        gps_count = 0
        for activity in self.client.get_activities(**filters):
            if self.only_run and activity.type != "Run":
                continue
            synced_count += 1
            route = resolve_activity_route(self.client, activity)
            if route:
                gps_count += 1
            if activity.map is None:
                activity.map = stravalib.model.Map()
            activity.map.summary_polyline = route or ""
            if IGNORE_BEFORE_SAVING and activity.map and activity.map.summary_polyline:
                activity.map.summary_polyline = filter_out(
                    activity.map.summary_polyline
                )
            #  strava use total_elevation_gain as elevation_gain
            activity.elevation_gain = activity.total_elevation_gain
            activity.subtype = activity.type
            created = update_or_create_activity(self.session, activity)
            if created:
                sys.stdout.write("+")
            else:
                sys.stdout.write(".")
            sys.stdout.flush()
        self.recover_hidden_routes()
        self.session.commit()
        print(
            f"\nSynced {synced_count} Strava activities "
            f"({gps_count} with source GPS routes)."
        )

    def recover_hidden_routes(self):
        """Refresh old summaries that privacy clipping would completely hide.

        Even a summary with several points can collapse a long run to a few
        meters. Check its detailed map before concluding no route can be shown.
        This also repairs older records outside the incremental sync window.
        """
        if IGNORE_BEFORE_SAVING:
            return
        recovered = 0
        for activity in self.session.query(Activity):
            source = activity.summary_polyline
            if not source or filter_out(source):
                continue
            try:
                detail = self.client.get_activity(activity.run_id)
            except ObjectNotFound:
                continue
            route = resolve_activity_route(self.client, detail)
            if route and route != source:
                activity.summary_polyline = route
                recovered += 1
        if recovered:
            print(f"\nRefreshed {recovered} routes hidden by simplified summaries.")

    def sync_from_data_dir(self, data_dir, file_suffix="gpx", activity_title_dict=None):
        loader = track_loader.TrackLoader()
        tracks = loader.load_tracks(
            data_dir, file_suffix=file_suffix, activity_title_dict=activity_title_dict
        )
        print(f"load {len(tracks)} tracks")
        if not tracks:
            print("No tracks found.")
            return

        synced_files = []

        for t in tracks:
            created = update_or_create_activity(
                self.session, t.to_namedtuple(run_from=file_suffix)
            )
            if created:
                sys.stdout.write("+")
            else:
                sys.stdout.write(".")
            synced_files.extend(t.file_names)
            sys.stdout.flush()

        save_synced_data_file_list(synced_files)

        self.session.commit()

    def sync_from_app(self, app_tracks):
        if not app_tracks:
            print("No tracks found.")
            return
        print("Syncing tracks '+' means new track '.' means update tracks")
        synced_files = []
        for t in app_tracks:
            created = update_or_create_activity(self.session, t)
            if created:
                sys.stdout.write("+")
            else:
                sys.stdout.write(".")
            if "file_names" in t:
                synced_files.extend(t.file_names)
            sys.stdout.flush()

        self.session.commit()

    def load(self):
        # if sub_type is not in the db, just add an empty string to it
        query = self.session.query(Activity).filter(Activity.distance > 0.1)
        if self.only_run:
            query = query.filter(Activity.type == "Run")

        activities = query.order_by(Activity.start_date_local)
        activity_list = []

        streak = 0
        last_date = None
        for activity in activities:
            # Determine running streak.
            date = datetime.datetime.strptime(  # noqa: DTZ007
                activity.start_date_local, "%Y-%m-%d %H:%M:%S"  # type: ignore
            ).date()
            if last_date is None:
                streak = 1
            elif date == last_date:
                pass
            elif date == last_date + datetime.timedelta(days=1):
                streak += 1
            else:
                assert date > last_date
                streak = 1
            last_date = date
            exported_activity = activity.to_dict()
            exported_activity["streak"] = streak
            if not IGNORE_BEFORE_SAVING:
                exported_activity["summary_polyline"] = filter_out(
                    exported_activity["summary_polyline"]
                )
            activity_list.append(exported_activity)

        # Missing GPS must stay missing. Borrowing another activity's route can
        # put a run in a different city and overwrite authentic source data.
        return activity_list

    def get_old_tracks_ids(self):
        try:
            activities = self.session.query(Activity).all()
            return [str(a.run_id) for a in activities]
        except Exception as e:  # noqa: BLE001
            # pass the error
            print(f"something wrong with {e!s}")
            return []

    def get_old_tracks_dates(self):
        try:
            activities = (
                self.session.query(Activity)
                .order_by(Activity.start_date_local.desc())
                .all()
            )
            return [str(a.start_date_local) for a in activities]
        except Exception as e:  # noqa: BLE001
            # pass the error
            print(f"something wrong with {e!s}")
            return []
