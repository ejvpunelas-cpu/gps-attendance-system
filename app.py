        if (
            not all(
                math.isfinite(v)
                for v in (lat, lon, accuracy)
            )
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
            or accuracy <= 0
            or accuracy > 100
        ):
            raise ValueError()

    except (ValueError, KeyError):
        return jsonify(
            ok=False,
            message='Invalid GPS reading'
        ), 400

    inside = (
        distance_m(
            CAMPUS_LATITUDE,
            CAMPUS_LONGITUDE,
            lat,
            lon
        ) <= CAMPUS_RADIUS_METERS
    )

    alerts = 0

    for schedule in active_classes_for_student(
        student,
        now
    ):
        if not class_is_active(schedule, now):
            continue

        record = Attendance.query.filter_by(
            user_id=student.id,
            schedule_id=schedule.id,
            attendance_date=now.date()
        ).first()

        if (
            not record
            or not record.check_in_time
            or record.check_out_time
        ):
            continue

        if inside:
            record.last_inside_at = now

        elif not record.departure_alerted:
            message = (
                f'{student.username} appears outside campus '
                f'during {schedule.subject} ({schedule.section}) '
                f'at {now.strftime("%I:%M %p")}.'
            )

            notice = Notice(
                teacher_id=schedule.teacher_id,
                student_id=student.id,
                schedule_id=schedule.id,
                message=message
            )

            db.session.add(notice)

            record.departure_alerted = True
            alerts += 1

    db.session.commit()

    return jsonify(
        ok=True,
        inside=inside,
        alerts_created=alerts
    )


# ==========================================
# MARK TEACHER NOTICE AS READ
# ==========================================

@app.route(
    '/teacher/notices/<int:notice_id>/read',
    methods=['POST']
)
@login_required
@role_required('teacher')
def read_notice(notice_id):
    notice = Notice.query.filter_by(
        id=notice_id,
        teacher_id=session['user_id']
    ).first_or_404()

    notice.read = True
    db.session.commit()

    return redirect(
        url_for('teacher_dashboard')
    )


# ==========================================
# RUN THE APPLICATION
# ==========================================

@app.route("/gps-test")
def gps_test():
    if "user_id" not in session:
        return redirect(url_for("login"))

    return render_template("gps_test.html")


if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 5000)),
        debug=os.environ.get('FLASK_DEBUG') == '1'
    )
