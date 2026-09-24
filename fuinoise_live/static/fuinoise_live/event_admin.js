document.addEventListener("DOMContentLoaded", () => {
    const community = document.getElementById("id_community");
    const timeZone = document.getElementById("id_event_time_zone");
    if (community && timeZone && community.dataset.communityTimeZones) {
        const defaults = JSON.parse(community.dataset.communityTimeZones);
        let previousDefault = defaults[community.value] || "";

        if (!timeZone.value && previousDefault) {
            timeZone.value = previousDefault;
        }

        community.addEventListener("change", () => {
            const nextDefault = defaults[community.value] || "";
            if (!timeZone.value || timeZone.value === previousDefault) {
                timeZone.value = nextDefault;
            }
            previousDefault = nextDefault;
        });
    }

    const eventDate = document.getElementById("id_date");
    if (!eventDate) return;

    const fillSlotDates = (previousDate = "") => {
        document.querySelectorAll('input[name^="raidslot_set-"][name$="-start_date"]').forEach((slotDate) => {
            if (slotDate.closest(".empty-form")) return;
            if (!slotDate.value || (previousDate && slotDate.value === previousDate)) {
                slotDate.value = eventDate.value;
            }
        });
    };

    fillSlotDates();
    let previousDate = eventDate.value;
    const syncEventDate = () => {
        fillSlotDates(previousDate);
        previousDate = eventDate.value;
    };
    for (const eventName of ["input", "change", "focus"]) {
        eventDate.addEventListener(eventName, syncEventDate);
    }
    if (window.django && django.jQuery) {
        django.jQuery(document).on("formset:added", () => fillSlotDates());
    }
});
