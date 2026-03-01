from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from resources.models import Resource
from resources.services import schedule_service
from tenants.permissions import IsTenantAuthorized


class IntegrationAvailabilityView(APIView):
    permission_classes = [IsTenantAuthorized]

    def get(self, request):
        mode = request.query_params.get("mode", "raw")
        resource_id_raw = request.query_params.get("resource_id")
        start_str = request.query_params.get("start")
        end_str = request.query_params.get("end")

        try:
            events = schedule_service.list_events(
                tenant=request.tenant,
                resource_id_raw=resource_id_raw,
                start_str=start_str,
                end_str=end_str,
                mode=mode,
            )
            return Response(events)
        except schedule_service.ScheduleNotFoundError:
            return Response([], status=200)
        except schedule_service.ScheduleValidationError as exc:
            return Response({"error": str(exc)}, status=400)

    def post(self, request):
        resource_id_raw = request.data.get("resource_id")
        week_config = request.data.get("week_config")

        try:
            if week_config:
                result = schedule_service.create_recurring_availability(
                    tenant=request.tenant,
                    resource_id_raw=resource_id_raw,
                    range_start=request.data.get("range_start"),
                    range_end=request.data.get("range_end"),
                    week_config=week_config,
                )
            else:
                result = schedule_service.create_single_availability(
                    tenant=request.tenant,
                    resource_id_raw=resource_id_raw,
                    start_str=request.data.get("start"),
                    end_str=request.data.get("end"),
                )

            return Response(result, status=201)
        except schedule_service.ScheduleNotFoundError:
            return Response({"error": "Resource not found"}, status=404)
        except schedule_service.ScheduleValidationError as exc:
            code = status.HTTP_409_CONFLICT if str(exc) == "Time slot conflict" else 400
            return Response({"error": str(exc)}, status=code)

    def delete(self, request, pk=None):
        availability_id = pk or request.data.get("id")
        try:
            schedule_service.delete_availability(request.tenant, availability_id)
            return Response(status=204)
        except schedule_service.ScheduleValidationError as exc:
            return Response({"error": str(exc)}, status=400)
        except schedule_service.ScheduleNotFoundError as exc:
            return Response({"error": str(exc)}, status=404)


class RecurringConfigView(APIView):
    permission_classes = [IsTenantAuthorized]

    def get(self, request):
        resource_id_raw = request.query_params.get("resource_id")

        try:
            data = schedule_service.get_recurring_config(request.tenant, resource_id_raw)
            return Response(data)
        except schedule_service.ScheduleNotFoundError:
            return Response({})


class ScheduleTemplateView(APIView):
    permission_classes = [IsTenantAuthorized]

    def get(self, request):
        resource_id_raw = request.query_params.get("resource_id")
        try:
            templates = schedule_service.list_templates(request.tenant, resource_id_raw)
            return Response(templates)
        except schedule_service.ScheduleValidationError as exc:
            return Response({"error": str(exc)}, status=400)
        except schedule_service.ScheduleNotFoundError:
            return Response({"error": "Resource not found"}, status=404)

    def post(self, request):
        try:
            result = schedule_service.save_template(
                tenant=request.tenant,
                resource_id_raw=request.data.get("resource_id"),
                name=request.data.get("name"),
                week_config=request.data.get("week_config"),
            )
            return Response(result, status=201)
        except schedule_service.ScheduleValidationError as exc:
            return Response({"error": str(exc)}, status=400)
        except schedule_service.ScheduleNotFoundError:
            return Response({"error": "Resource not found"}, status=404)

    def delete(self, request):
        try:
            schedule_service.delete_template(request.tenant, request.data.get("id"))
            return Response(status=204)
        except schedule_service.ScheduleValidationError as exc:
            return Response({"error": str(exc)}, status=400)


class IntegrationResourceView(APIView):
    permission_classes = [IsTenantAuthorized]

    def post(self, request):
        external_id = request.data.get("external_id")
        name = request.data.get("name")
        email = request.data.get("email")

        if not all([external_id, name]):
            return Response({"error": "Missing external_id or name"}, status=400)

        resource, created = Resource.objects.update_or_create(
            tenant=request.tenant,
            external_id=external_id,
            defaults={"name": name, "email": email or ""},
        )
        return Response(
            {"saas_id": str(resource.id), "status": "created" if created else "updated"},
            status=201,
        )
