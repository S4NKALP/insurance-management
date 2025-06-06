from django.db import models
from django.contrib.auth.models import User, AbstractUser
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.utils.timezone import now
from django.db.models import Sum
from django.db.models import Sum, Avg, Count, F
from typing import Dict, Union
from django.dispatch import receiver
import logging
import re
logger = logging.getLogger(__name__)
from .constants import (
    GENDER_CHOICES,
    POLICY_TYPES,
    DOCUMENT_TYPES,
    PROVINCE_CHOICES,
    REASON_CHOICES,
    STATUS_CHOICES,
    TIME_PERIOD_CHOICES,
    PROCESSING_STATUS_CHOICES,
    EMPLOYEE_STATUS_CHOICES,
    EXE_FREQ_CHOICE,
    RISK_CHOICES,
    PAYMENT_CHOICES,
)
import logging

logger = logging.getLogger(__name__)


# Create your models here.
class Occupation(models.Model):
    name = models.CharField(max_length=100, unique=True)
    risk_category = models.CharField(
        max_length=50,
        choices=[
            ("Low", "Low Risk"),
            ("Moderate", "Moderate Risk"),
            ("High", "High Risk"),
        ],
        default="Moderate",
    )

    def __str__(self):
        return self.name


class MortalityRate(models.Model):
    age_group_start = models.PositiveIntegerField(null=True, blank=True)
    age_group_end = models.PositiveIntegerField(null=True, blank=True)
    rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=0.00, null=True, blank=True
    )

    class Meta:
        unique_together = ("age_group_start", "age_group_end")

    def __str__(self):
        return f"{self.age_group_start}-{self.age_group_end}: {self.rate}%"


class Company(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255, unique=True)
    company_code = models.IntegerField(unique=True, default=1)
    address = models.CharField(max_length=255)
    logo = models.ImageField(upload_to="company", null=True, blank=True)
    email = models.EmailField(max_length=255)
    is_active = models.BooleanField(default=True)
    phone_number = models.CharField(max_length=20)

    class Meta:
        verbose_name = "Company"
        verbose_name_plural = "Companies"
        indexes = [
            models.Index(fields=["name"]),
        ]

    def __str__(self):
        return self.name


class Branch(models.Model):
    name = models.CharField(max_length=255)
    branch_code = models.IntegerField(unique=True, default=1)
    location = models.CharField(max_length=255, null=True, blank=True)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="branches", default=1
    )

    class Meta:
        verbose_name = "Branch"
        verbose_name_plural = "Branches"

    def __str__(self):
        return f"{self.name} ({self.branch_code})"


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employees",
    )
    company = models.ForeignKey(
        Company, on_delete=models.SET_NULL, null=True, blank=True
    )

    def __str__(self):
        return f"{self.user.username}'s Profile"

    def save(self, *args, **kwargs):
        if self.branch and not self.company:
            self.company = self.branch.company
        super().save(*args, **kwargs)


# Basic Information about Insurance Policies


class InsurancePolicy(models.Model):
    name = models.CharField(max_length=200)
    policy_type = models.CharField(max_length=50, choices=POLICY_TYPES, default="Term")
    base_multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=1.0)
    min_sum_assured = models.DecimalField(
        max_digits=12, decimal_places=2, default=500.00
    )
    max_sum_assured = models.DecimalField(
        max_digits=12, decimal_places=2, default=10000.00
    )
    include_adb = models.BooleanField(default=False)
    include_ptd = models.BooleanField(default=False)
    adb_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=0.00
    )  # ADB charge %
    ptd_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=0.00
    )  # PTD charge %
    description = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Insurance Policy"
        verbose_name_plural = "Insurance Policies"

    def clean(self):
        super().clean()
        if self.policy_type == "Term" and self.base_multiplier != 1.0:
            raise ValidationError(
                "Base multiplier for Term insurance must always be 1.0."
            )


# Guranteed Surrender Value
class GSVRate(models.Model):
    policy = models.ForeignKey(
        "InsurancePolicy", on_delete=models.CASCADE, related_name="gsv_rates"
    )
    min_year = models.PositiveIntegerField(help_text="Minimum year of the range.")
    max_year = models.PositiveIntegerField(help_text="Maximum year of the range.")
    rate = models.DecimalField(
        max_digits=5, decimal_places=2, help_text="GSV rate as a percentage."
    )

    def clean(self):
        """Ensure the year range is valid and does not overlap with other GSV ranges."""
        if self.min_year >= self.max_year:
            raise ValidationError("Minimum year must be less than maximum year.")

        # Get all existing ranges for the policy
        existing_ranges = GSVRate.objects.filter(policy=self.policy).exclude(
            pk=self.pk
        )  # Exclude the current instance

        # Check for overlaps using strict inequality for ranges
        overlapping = existing_ranges.filter(
            models.Q(
                # New range starts strictly before existing range ends
                min_year__lt=self.max_year,
                # AND existing range ends strictly after new range starts
                max_year__gt=self.min_year,
            )
        )

        if overlapping.exists():
            raise ValidationError("GSV year ranges cannot overlap for the same policy.")

    def __str__(self):
        return f"GSV Rate {self.rate}% for {self.min_year}-{self.max_year} years"


# SSv Factor
class SSVConfig(models.Model):
    policy = models.ForeignKey(
        "InsurancePolicy", on_delete=models.CASCADE, related_name="ssv_configs"
    )
    min_year = models.PositiveIntegerField(help_text="Minimum year of the range.")
    max_year = models.PositiveIntegerField(help_text="Maximum year of the range.")
    ssv_factor = models.DecimalField(
        max_digits=5, decimal_places=2, help_text="SSV factor as a percentage."
    )
    eligibility_years = models.PositiveIntegerField(
        default=5, help_text="Years of premium payment required for SSV eligibility."
    )
    custom_condition = models.TextField(
        blank=True, help_text="Optional custom condition for SSV."
    )

    def clean(self):
        """Ensure the year range is valid and does not overlap with other SSV ranges."""
        if self.min_year >= self.max_year:
            raise ValidationError("Minimum year must be less than maximum year.")

        overlapping = SSVConfig.objects.filter(
            policy=self.policy, min_year__lte=self.max_year, max_year__gte=self.min_year
        ).exclude(pk=self.pk)  # Exclude the current instance

        if overlapping.exists():
            raise ValidationError("SSV year ranges cannot overlap for the same policy.")

    def __str__(self):
        return (
            f"SSV Factor {self.ssv_factor}% for {self.min_year}-{self.max_year} years"
        )


#  Agent Application


class AgentApplication(models.Model):
    id = models.BigAutoField(primary_key=True)
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="agent_applications", default=1
    )
    first_name = models.CharField(max_length=200)
    last_name = models.CharField(max_length=50)
    father_name = models.CharField(max_length=200)
    mother_name = models.CharField(max_length=200)
    grand_father_name = models.CharField(max_length=200, null=True, blank=True)
    grand_mother_name = models.CharField(max_length=200, null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, default="Male")
    email = models.EmailField(max_length=200, unique=True)
    phone_number = models.CharField(max_length=15)
    address = models.CharField(max_length=200)
    resume = models.FileField(upload_to="agent_application", null=True, blank=True)
    citizenship_front = models.ImageField(
        upload_to="agent_application", null=True, blank=True
    )
    citizenship_back = models.ImageField(
        upload_to="agent_application", null=True, blank=True
    )
    license_front = models.ImageField(
        upload_to="agent_application", null=True, blank=True
    )
    license_back = models.ImageField(
        upload_to="agent_application", null=True, blank=True
    )
    pp_photo = models.ImageField(upload_to="agent_application", null=True, blank=True)
    license_number = models.CharField(max_length=50, null=True, blank=True)
    license_issue_date = models.DateField(null=True, blank=True)
    license_expiry_date = models.DateField(null=True, blank=True)
    license_type = models.CharField(max_length=50, null=True, blank=True)
    license_issue_district = models.CharField(max_length=50, null=True, blank=True)
    license_issue_zone = models.CharField(max_length=50, null=True, blank=True)
    license_issue_province = models.CharField(max_length=50, null=True, blank=True)
    license_issue_country = models.CharField(max_length=50, null=True, blank=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default="Pending")
    created_at = models.DateField(default=date.today)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"
    def clean(self):
        """Validate the phone number format."""
        errors = {}  # Initialize the errors dictionary
        phone_pattern = r'^\d{10}$'
        if self.phone_number and not re.match(phone_pattern, self.phone_number):
            errors["phone_number"] = "Phone number must be exactly 10 digits."

        if errors:
            raise ValidationError(errors)
    class Meta:
        verbose_name = "Agent Application"
        verbose_name_plural = "Agent Applications"
        indexes = [
            models.Index(fields=["branch"]),
            models.Index(fields=["status"]),
        ]


# Agent Application ends
# Sales Agent


class SalesAgent(models.Model):
    id = models.BigAutoField(primary_key=True)
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="sales_agents", default=1
    )
    application = models.OneToOneField(
        AgentApplication,
        on_delete=models.SET_NULL,
        related_name="sales_agent",
        null=True,
        blank=True,
    )

    agent_code = models.CharField(max_length=50, unique=True, default=1)
    is_active = models.BooleanField(default=True)
    joining_date = models.DateField(default=date.today)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    total_policies_sold = models.IntegerField(default=0)
    total_premium_collected = models.DecimalField(
        max_digits=12, decimal_places=2, default=0.00
    )
    last_policy_date = models.DateField(null=True, blank=True)
    termination_date = models.DateField(null=True, blank=True)
    termination_reason = models.CharField(max_length=200, null=True, blank=True)

    status = models.CharField(
        max_length=20, choices=EMPLOYEE_STATUS_CHOICES, default="ACTIVE"
    )

    def __str__(self):
        if self.application:
            return f"{self.application.first_name} {self.application.last_name} ({self.agent_code})"
        return self.agent_code

    def get_full_name(self):
        if self.application:
            return f"{self.application.first_name} {self.application.last_name}"
        return None

    class Meta:
        verbose_name = "Sales Agent"
        verbose_name_plural = "Sales Agents"
        indexes = [
            models.Index(fields=["branch"]),
            models.Index(fields=["total_policies_sold"]),
            models.Index(fields=["status"]),
        ]


class DurationFactor(models.Model):
    min_duration = models.PositiveIntegerField(help_text="Minimum duration in years")
    max_duration = models.PositiveIntegerField(help_text="Maximum duration in years")
    factor = models.DecimalField(max_digits=5, decimal_places=2)
    policy_type = models.CharField(max_length=50, choices=POLICY_TYPES)

    class Meta:
        unique_together = ["min_duration", "max_duration", "policy_type"]
        ordering = ["min_duration"]

    def clean(self):
        if self.min_duration >= self.max_duration:
            raise ValidationError("Minimum duration must be less than maximum duration")

        overlapping = DurationFactor.objects.filter(
            policy_type=self.policy_type,
            min_duration__lte=self.max_duration,
            max_duration__gte=self.min_duration,
        ).exclude(pk=self.pk)

        if overlapping.exists():
            raise ValidationError(
                "Duration ranges cannot overlap for the same policy type"
            )

    def __str__(self):
        return f"{self.policy_type} ({self.min_duration}-{self.max_duration} years): {self.factor}x"


# policy holders start


class PolicyHolder(models.Model):
    id = models.BigAutoField(primary_key=True)
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="policy_holders", default=1
    )
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, blank=True, null=True)
    policy_number = models.IntegerField(unique=True, default="", blank=True, null=True)
    agent = models.ForeignKey(
        SalesAgent, on_delete=models.CASCADE, null=True, blank=True
    )
    policy = models.ForeignKey(
        InsurancePolicy,
        related_name="policy_holders",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    duration_years = models.PositiveIntegerField(default=1)
    sum_assured = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    first_name = models.CharField(max_length=200)
    middle_name = models.CharField(max_length=50, null=True, blank=True)
    last_name = models.CharField(max_length=50)
    gender = models.CharField(
        max_length=1,
        blank=True,
        null=True,
        choices=[("M", "Male"), ("F", "Female"), ("O", "Other")],
    )
    date_of_birth = models.DateField(null=True, blank=True)
    age = models.PositiveIntegerField(editable=False, null=True)
    phone_number = models.CharField(max_length=15, null=True, blank=True)
    emergency_contact_name = models.CharField(max_length=200, blank=True, null=True)
    emergency_contact_number = models.CharField(max_length=15, blank=True, null=True)

    document_number = models.CharField(max_length=50, default=1)
    document_type = models.CharField(
        max_length=111, blank=True, null=True, choices=DOCUMENT_TYPES
    )
    document_front = models.ImageField(upload_to="policyHolder")
    document_back = models.ImageField(upload_to="policyHolder")
    pan_number = models.CharField(max_length=20, blank=True, null=True)
    pan_front = models.ImageField(upload_to="policy_holders", null=True, blank=True)
    pan_back = models.ImageField(upload_to="policy_holders", null=True, blank=True)

    pp_photo = models.ImageField(upload_to="policyHolder")
    dietary_habits = models.TextField(blank=True, null=True)
    nominee_name = models.CharField(max_length=200, null=True, blank=True)
    nominee_document_type = models.CharField(
        max_length=111, blank=True, null=True, choices=DOCUMENT_TYPES
    )
    nominee_document_number = models.PositiveIntegerField(null=True, blank=True)
    nominee_document_front = models.ImageField(upload_to="policyHolder")
    nominee_document_back = models.ImageField(upload_to="policyHolder")
    nominee_pp_photo = models.ImageField(upload_to="policyHolder")
    nominee_relation = models.CharField(max_length=255)
    province = models.CharField(
        max_length=255, choices=PROVINCE_CHOICES, default="Karnali"
    )
    district = models.CharField(max_length=255)
    municipality = models.CharField(max_length=255)
    ward = models.CharField(max_length=255)
    nearest_hospital = models.CharField(max_length=255, blank=True, null=True)
    natural_hazard_exposure = models.CharField(
        max_length=50, choices=RISK_CHOICES, blank=True, null=True
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="Pending")
    work_environment_risk = models.CharField(
        max_length=50, choices=RISK_CHOICES, blank=True, null=True
    )
    policy = models.ForeignKey(
        InsurancePolicy,
        related_name="policy_holders",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    health_history = models.CharField(max_length=500, null=True, blank=True)
    habits = models.CharField(max_length=500, null=True, blank=True)
    exercise_frequency = models.CharField(
        max_length=50, choices=EXE_FREQ_CHOICE, blank=True, null=True
    )
    alcoholic = models.BooleanField(default=False)
    smoker = models.BooleanField(default=False)
    include_adb = models.BooleanField(default=False)
    include_ptd = models.BooleanField(default=False)
    past_medical_report = models.FileField(
        upload_to="policy_holders", null=True, blank=True
    )
    family_medical_history = models.TextField(null=True, blank=True)
    recent_medical_reports = models.FileField(
        upload_to="policy_holders", blank=True, null=True
    )
    yearly_income = models.CharField(max_length=455, default=500000)
    occupation = models.ForeignKey(
        Occupation, on_delete=models.SET_NULL, null=True, blank=True
    )
    assets_details = models.TextField(max_length=5000, null=True, blank=True)
    payment_interval = models.CharField(
        max_length=20,
        choices=[
            ("Single", "Single"),
            ("quarterly", "Quarterly"),
            ("semi_annual", "Semi-Annual"),
            ("annual", "Annual"),
        ],
        default="annual",
    )
    payment_mode = models.CharField(
        max_length=50,
        choices=[
            ("Cash", "Cash"),
            ("Bank Transfer", "Bank Transfer"),
            ("Online Payment", "Online Payment"),
        ],
        default="Online Payment",
    )
    risk_category = models.CharField(
        max_length=50,
        choices=[
            ("Low", "Low Risk"),
            ("Moderate", "Moderate Risk"),
            ("High", "High Risk"),
        ],
        default="Moderate",
        blank=True,
        help_text="Risk category assigned based on underwriting.",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Pending")
    payment_status = models.CharField(
        max_length=50, choices=PROCESSING_STATUS_CHOICES, default="Due"
    )
    start_date = models.DateField(default=date.today)

    maturity_date = models.DateField(null=True, blank=True)
    def clean(self):
        """Validate the phone number format."""
        errors = {}  # Initialize the errors dictionary
        phone_pattern = r'^\d{10}$'
        if self.phone_number and not re.match(phone_pattern, self.phone_number):
            errors["phone_number"] = "Phone number must be exactly 10 digits."

        if errors:
            raise ValidationError(errors)
    def clean(self):
        errors = {}
        if self.sum_assured:
            if self.sum_assured < self.policy.min_sum_assured:
                errors["sum_assured"] = (
                    f"Sum assured must be at least {self.policy.min_sum_assured}."
                )
        elif self.sum_assured > self.policy.max_sum_assured:
            errors["sum_assured"] = (
                f"Sum assured cannot exceed {self.policy.max_sum_assured}."
            )
        if self.date_of_birth:
            age = self.calculate_age()
        if age < 18 or age > 60:
            errors["date_of_birth"] = (
                f"Age must be between 18 and 60. Current age: {age}."
            )
        if errors:
            raise ValidationError(errors)

    def calculate_age(self):
        """Calculate age based on date of birth"""
        if self.date_of_birth:
            today = now().date()
            return (
                today.year
                - self.date_of_birth.year
                - (
                    (today.month, today.day)
                    < (self.date_of_birth.month, self.date_of_birth.day)
                )
            )
        return None

    def calculate_maturity_date(self):
        """Calculate maturity date based on start date and duration"""
        if self.start_date and self.duration_years:
            return self.start_date.replace(
                year=self.start_date.year + self.duration_years
            )
        return None

    def generate_policy_number(self):
        """Generate a unique policy number."""
        if not self.company or not self.branch:
            return None

        try:
            last_holder = (
                PolicyHolder.objects.filter(company=self.company, branch=self.branch)
                .exclude(policy_number__isnull=True)
                .order_by("-policy_number")
                .first()
            )

            last_number = int(str(last_holder.policy_number)[-5:]) if last_holder else 0
            new_number = last_number + 1
            return f"{self.company.company_code}{self.branch.branch_code}{str(new_number).zfill(5)}"
        except Exception as e:
            raise ValueError(f"Error generating policy number: {e}")

        def save(self, *args, **kwargs):
            """Override save to handle automatic field updates."""
            if not self.policy_number:
                self.policy_number = self.generate_policy_number()
            super().save(*args, **kwargs)

    def save(self, *args, **kwargs):
        """Override save method to handle automatic field updates"""
        # Calculate age if date of birth is provided
        if self.date_of_birth:
            self.age = self.calculate_age()

        # Generate policy number if status is Active and number doesn't exist
        if self.status == "Active" and not self.policy_number:
            self.policy_number = self.generate_policy_number()

        # Set maturity date if not already set
        if not self.maturity_date:
            self.maturity_date = self.calculate_maturity_date()

        # Run full validation
        self.full_clean()

        super().save(*args, **kwargs)

    def __str__(self):
        """String representation of the policy holder"""
        policy_num = self.policy_number if self.policy_number else "Pending"
        return f"{self.first_name} {self.last_name} ({policy_num})"

    class Meta:
        indexes = [
            models.Index(fields=["branch"]),
            models.Index(fields=["policy"]),
        ]


# policy holders end


# Bonus Rate model


class BonusRate(models.Model):
    year = models.PositiveIntegerField(
        default=date.today().year,  # ✅ Default to current year
        help_text="Year the bonus rate applies to",
    )
    policy_type = models.CharField(
        max_length=50,
        choices=POLICY_TYPES,
        default="Term",  # Ensure POLICY_TYPES is defined
        help_text="Applicable policy type",
    )
    min_year = models.PositiveIntegerField(
        help_text="Minimum policy duration in years", default=1
    )
    max_year = models.PositiveIntegerField(
        help_text="Maximum policy duration in years", default=9
    )
    bonus_per_thousand = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Bonus amount per 1000 of sum assured",
        default=0.00,
    )

    class Meta:
        unique_together = ["policy_type", "min_year", "max_year"]
        ordering = ["policy_type", "min_year"]

    def __str__(self):
        return f"{self.policy_type}: {self.min_year}-{self.max_year} years -> {self.bonus_per_thousand} per 1000"

    @classmethod
    def get_bonus_rate(cls, policy_type, duration):
        """Fetch the correct bonus rate based on policy type and duration."""
        return cls.objects.filter(
            policy_type=policy_type, min_year__lte=duration, max_year__gte=duration
        ).first()


# Bonus Model
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


class Bonus(models.Model):
    policy_holder = models.ForeignKey(
        PolicyHolder, on_delete=models.CASCADE, related_name="bonuses"
    )
    bonus_type = models.CharField(
        max_length=20,
        choices=[("SI", "Simple Interest"), ("CI", "Compound Interest")],
        default="SI",
    )
    accrued_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, editable=False
    )
    start_date = models.DateField(help_text="Start date for bonus accrual.")

    def calculate_bonus(self):
        """Calculate yearly bonus based on policy type, duration, and sum assured."""
        try:
            policy = self.policy_holder.policy
            duration = self.policy_holder.duration_years
            sum_assured = self.policy_holder.sum_assured
            # Fetch applicable bonus rate
            bonus_rate_obj = BonusRate.get_bonus_rate(policy.policy_type, duration)

            logger.info(f"Bonus Rate Object: {bonus_rate_obj}")

            if not bonus_rate_obj:
                logger.warning("No bonus rate found for policy type and duration.")
                return Decimal(0)  # No bonus if rate is not defined

            bonus_per_1000 = Decimal(str(bonus_rate_obj.bonus_per_thousand))
            sum_assured = Decimal(str(sum_assured))

            logger.info(f"Bonus per 1000: {bonus_per_1000}, Sum Assured: {sum_assured}")

            total_bonus = (sum_assured / Decimal(1000)) * bonus_per_1000

            logger.info(f"Total Bonus: {total_bonus}")

            return total_bonus.quantize(Decimal("1.00"))

        except Exception as e:
            logger.error(f"Error calculating bonus: {e}")
            raise ValidationError(f"Error calculating bonus: {e}")

    def save(self, *args, **kwargs):
        """Override save to calculate bonus before saving."""
        self.accrued_amount = self.calculate_bonus()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Bonus for {self.policy_holder} on {self.start_date}"


# claim requestes


class ClaimRequest(models.Model):
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="claim_requests", default=1
    )
    policy_holder = models.ForeignKey(
        PolicyHolder, on_delete=models.CASCADE, related_name="claim_requests"
    )
    claim_date = models.DateField(auto_now_add=True)
    status = models.CharField(
        max_length=50,
        choices=[
            ("Pending", "Pending"),
            ("Approved", "Approved"),
            ("Rejected", "Rejected"),
        ],
        default="Pending",
    )
    bill = models.ImageField(upload_to="claim_processing", null=True, blank=True)
    policy_copy = models.ImageField(upload_to="claim_processing", null=True, blank=True)
    health_report = models.ImageField(
        upload_to="claim_processing", null=True, blank=True
    )
    reason = models.CharField(max_length=50, choices=REASON_CHOICES, default="Others")
    other_reason = models.CharField(max_length=500, null=True, blank=True)
    claim_amount = models.DecimalField(max_digits=12, decimal_places=2, editable=False)

    def calculate_claim_amount(self):
        """Calculate the claimable amount."""
        sum_assured = self.policy_holder.sum_assured or Decimal(0)
        outstanding_loans = self.policy_holder.loans.filter(
            loan_status="Active"
        ).aggregate(total=Sum("remaining_balance"))["total"] or Decimal(0)
        return max(sum_assured - outstanding_loans, Decimal(0))

    def save(self, *args, **kwargs):
        """Auto-calculate claim amount before saving."""
        self.claim_amount = self.calculate_claim_amount()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Claim {self.id} - {self.policy_holder}"

    class Meta:
        verbose_name = "Claim Request"
        verbose_name_plural = "Claim Requests"


# Claim Processing
class ClaimProcessing(models.Model):
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="claim_processings", default=1
    )
    claim_request = models.OneToOneField(
        ClaimRequest, on_delete=models.CASCADE, related_name="processing"
    )
    processing_status = models.CharField(
        max_length=50,
        choices=[
            ("In Progress", "In Progress"),
            ("Approved", "Approved"),
            ("Rejected", "Rejected"),
        ],
        default="In Progress",
    )
    remarks = models.TextField(null=True, blank=True)
    processing_date = models.DateField(auto_now=True)

    def finalize_claim(self):
        """Finalize claim based on approval."""
        if self.processing_status == "Approved":
            PaymentProcessing.objects.create(
                company=self.company,
                name=f"Claim Settlement - {self.claim_request.policy_holder}",
                claim_request=self.claim_request,
                processing_status="Completed",
            )
            self.claim_request.status = "Approved"
        elif self.processing_status == "Rejected":
            self.claim_request.status = "Rejected"

        self.claim_request.save()

    def save(self, *args, **kwargs):
        """Finalize claim on save."""
        super().save(*args, **kwargs)
        self.finalize_claim()

    def __str__(self):
        return f"Processing {self.id} - {self.processing_status}"

    class Meta:
        verbose_name = "Claim Processing"
        verbose_name_plural = "Claim Processings"


# Employee and Roles
class EmployeePosition(models.Model):
    id = models.BigAutoField(primary_key=True)
    position = models.CharField(max_length=50)

    def __str__(self):
        return self.position

    class Meta:
        verbose_name = "Employee Position"
        verbose_name_plural = "Employee Positions"


class Employee(models.Model):
    id = models.BigAutoField(primary_key=True)
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=50)
    address = models.CharField(max_length=100)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    date_of_birth = models.DateField()
    employee_position = models.ForeignKey(
        EmployeePosition,
        related_name="employees",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Employee"
        verbose_name_plural = "Employees"


# Payment Processing
class PaymentProcessing(models.Model):
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="payment_processings", default=1
    )
    name = models.CharField(max_length=200)
    processing_status = models.CharField(
        max_length=50,
        choices=[("Pending", "Pending"), ("Completed", "Completed")],
        default="Pending",
    )
    claim_request = models.OneToOneField(
        ClaimRequest,
        on_delete=models.CASCADE,
        related_name="payment",
        null=True,
        blank=True,
    )
    date_of_processing = models.DateField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Payment Processing"
        verbose_name_plural = "Payment Processings"


# Underwriting Process Or report
class Underwriting(models.Model):
    policy_holder = models.OneToOneField(
        "PolicyHolder", on_delete=models.CASCADE, related_name="underwriting"
    )
    risk_assessment_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        help_text="Calculated risk score (0-100).",
    )
    risk_category = models.CharField(
        max_length=50,
        choices=[
            ("Low", "Low Risk"),
            ("Moderate", "Moderate Risk"),
            ("High", "High Risk"),
        ],
        default="Moderate",
    )
    manual_override = models.BooleanField(
        default=False, help_text="Enable to manually update risk scores."
    )
    remarks = models.TextField(
        null=True, blank=True, help_text="Additional remarks about underwriting."
    )
    last_updated_by = models.CharField(
        max_length=50,
        choices=[("System", "System"), ("Admin", "Admin")],
        default="System",
    )
    last_updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # Only calculate risk if manual override is disabled
        if not self.manual_override:
            self.calculate_risk()
            self.last_updated_by = "System"
        else:
            self.last_updated_by = "Admin"
        super().save(*args, **kwargs)

    def calculate_risk(self):
        """Automatically calculate the risk score based on policyholder data."""
        try:
            age = self.policy_holder.age
            occupation_risk = 10  # Default risk value if occupation is None
            if self.policy_holder.occupation:
                occupation_risk = {"Low": 10, "Moderate": 20, "High": 30}.get(
                    self.policy_holder.occupation.risk_category, 10
                )

            # Age-based risk
            age_risk = 5 if age < 30 else (15 if age <= 50 else 25)

            # Health and lifestyle risks
            health_risk = 0
            if self.policy_holder.smoker:
                health_risk += 20
            if self.policy_holder.alcoholic:
                health_risk += 15

            # Final risk score
            total_risk = age_risk + occupation_risk + health_risk
            self.risk_assessment_score = min(total_risk, 100)
            self.risk_category = self.determine_risk_category()
        except Exception as e:
            raise ValidationError(f"Error calculating risk: {e}")

    def determine_risk_category(self):
        score = self.risk_assessment_score
        return "Low" if score < 40 else "Moderate" if score < 70 else "High"

    def __str__(self):
        return f"Underwriting for {self.policy_holder} ({self.risk_category})"


# Premium Payment Model


class PremiumPayment(models.Model):
    policy_holder = models.ForeignKey(
        "PolicyHolder", on_delete=models.CASCADE, related_name="premium_payments"
    )
    annual_premium = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, editable=False
    )
    interval_payment = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, editable=False
    )
    total_paid = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, editable=False
    )
    paid_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )  # Amount to be added
    next_payment_date = models.DateField(null=True, blank=True)
    fine_due = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_premium = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, editable=False
    )
    remaining_premium = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, editable=False
    )
    gsv_value = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, editable=False
    )
    ssv_value = models.DecimalField(
        max_digits=12, decimal_places=2, default=0, editable=False
    )
    payment_status = models.CharField(
        max_length=255, choices=PAYMENT_CHOICES, default="Unpaid"
    )

    def calculate_premium(self):
        """Calculate total and interval premiums for the policy."""
        try:
            policy = self.policy_holder.policy
            sum_assured = self.policy_holder.sum_assured
            duration_years = self.policy_holder.duration_years
            age = self.policy_holder.age  # Ensure this field exists in PolicyHolder

            if not policy or not sum_assured or not age:
                raise ValidationError(
                    "Policy, Sum Assured, and Age are required for premium calculation."
                )

            # Fetch mortality rate based on age range
            mortality_rate_obj = MortalityRate.objects.filter(
                age_group_start__lte=age, age_group_end__gte=age
            ).first()

            if not mortality_rate_obj:
                return Decimal("0.00"), Decimal("0.00")  # Instead of returning None

            mortality_rate = Decimal(mortality_rate_obj.rate)

            # Base premium calculation
            base_premium = (sum_assured * mortality_rate) / Decimal(100)

            # Fetch duration factor
            duration_factor_obj = DurationFactor.objects.filter(
                min_duration__lte=duration_years, max_duration__gte=duration_years
            ).first()

            if not duration_factor_obj:
                return Decimal("0.00"), Decimal("0.00")  # Instead of returning None

            duration_factor = Decimal(
                duration_factor_obj.factor
            )  # Ensure it's a Decimal

            # Adjust premium based on policy type
            if policy.policy_type == "Endownment":
                adjusted_premium = (
                    base_premium * Decimal(policy.base_multiplier) * duration_factor
                )
            elif policy.policy_type == "Term":
                adjusted_premium = base_premium
            else:
                raise ValidationError(f"Unsupported policy type: {policy.policy_type}")

            # Add ADB/PTD charges if applicable
            adb_charge = (
                (sum_assured * Decimal(policy.adb_percentage)) / Decimal(100)
                if policy.include_adb
                else Decimal("0.00")
            )
            ptd_charge = (
                (sum_assured * Decimal(policy.ptd_percentage)) / Decimal(100)
                if policy.include_ptd
                else Decimal("0.00")
            )

            annual_premium = adjusted_premium + adb_charge + ptd_charge

            # Calculate interval payments
            interval_mapping = {
                "quarterly": 4,
                "semi_annual": 2,
                "annual": 1,
                "Single": 1,
            }
            interval_count = interval_mapping.get(
                self.policy_holder.payment_interval, 1
            )
            interval_payment = annual_premium / Decimal(interval_count)

            return annual_premium.quantize(Decimal("1.00")), interval_payment.quantize(
                Decimal("1.00")
            )

        except ValidationError as e:
            raise ValidationError(f"Error calculating premium: {e}")

        return Decimal("0.00"), Decimal(
            "0.00"
        )  # Always return a tuple, even if an error occurs

    def calculate_gsv(self):
        """Calculate Guaranteed Surrender Value (GSV)."""
        try:
            duration_years = (date.today() - self.policy_holder.start_date).days // 365
            gsv_rate = self.policy_holder.policy.gsv_rates.filter(
                min_year__lte=duration_years, max_year__gte=duration_years
            ).first()

            if not gsv_rate:
                return Decimal("0.00")  # No GSV defined for current duration

            paid_premium = max(self.total_paid - self.annual_premium, Decimal("0.00"))
            return (paid_premium * gsv_rate.rate / Decimal(100)).quantize(
                Decimal("1.00")
            )
        except Exception as e:
            raise ValidationError(f"Error calculating GSV: {e}")

    def calculate_ssv(self):
        """Calculate Special Surrender Value (SSV)."""
        if self.policy_holder.policy.policy_type != "Endownment":
            return Decimal(0)  # SSV only applies to endowment policie
        else:
            try:
                duration_years = (
                    date.today() - self.policy_holder.start_date
                ).days // 365
                premiums_paid = self.policy_holder.premium_payments.count()

                # Get applicable SSV configuration
                applicable_range = self.policy_holder.policy.ssv_configs.filter(
                    min_year__lte=duration_years, max_year__gte=duration_years
                ).first()

                if (
                    not applicable_range
                    or premiums_paid < applicable_range.eligibility_years
                ):
                    return Decimal("0.00")

                # Total Bonuses
                total_bonuses = self.policy_holder.bonuses.aggregate(
                    total=Sum("accrued_amount")
                )["total"] or Decimal("0.00")

                # Calculate SSV
                premium_component = self.total_paid * (
                    applicable_range.ssv_factor / Decimal(100)
                )
                ssv = premium_component + total_bonuses

                return ssv.quantize(Decimal("1.00"))
            except Exception as e:
                raise ValidationError(f"Error calculating SSV: {e}")

    def save(self, *args, **kwargs):
        if not self.pk:  # New instance
            self.annual_premium, self.interval_payment = self.calculate_premium()

            if self.policy_holder.payment_interval == "Single":
                self.total_premium = self.interval_payment
            else:
                self.total_premium = self.annual_premium * Decimal(
                    str(self.policy_holder.duration_years)
                )

        # Convert paid_amount to Decimal if it's not already
        if isinstance(self.paid_amount, float):
            self.paid_amount = Decimal(str(self.paid_amount))

        # Handle new payment if paid_amount is provided
        if self.paid_amount > 0:
            if isinstance(self.total_paid, float):
                self.total_paid = Decimal(str(self.total_paid))
            self.total_paid += self.paid_amount
            self.paid_amount = Decimal(
                "0.00"
            )  # Reset paid_amount after adding to total_paid

        # Update remaining premium and payment status
        self.remaining_premium = max(
            self.total_premium - self.total_paid, Decimal("0.00")
        )

        if self.total_paid >= self.total_premium:
            self.payment_status = "Paid"
        elif self.total_paid > 0:
            self.payment_status = "Partially Paid"
        else:
            self.payment_status = "Unpaid"

        # Handle fine
        if self.fine_due > 0:
            if isinstance(self.fine_due, float):
                self.fine_due = Decimal(str(self.fine_due))
            if isinstance(self.interval_payment, float):
                self.interval_payment = Decimal(str(self.interval_payment))
                self.interval_payment += self.fine_due
                self.fine_due = Decimal("0.00")

        # Set next payment date
        if (
            not self.next_payment_date
            and self.policy_holder.payment_interval != "Single"
        ):
            interval_months = {"quarterly": 3, "semi_annual": 6, "annual": 12}.get(
                self.policy_holder.payment_interval
            )

            if interval_months:
                today = date.today()
                self.next_payment_date = today.replace(
                    month=((today.month - 1 + interval_months) % 12) + 1,
                    year=today.year + ((today.month - 1 + interval_months) // 12),
                )
        # --- New GSV and SSV Calculations ---
        self.gsv_value = self.calculate_gsv()
        self.ssv_value = self.calculate_ssv()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Premium Payment"
        verbose_name_plural = "Premium Payments"

    def __str__(self):
        return f"Premium Payment - {self.policy_holder.first_name} {self.policy_holder.last_name} ({self.payment_status})"


# Agent Report
class AgentReport(models.Model):
    agent = models.ForeignKey(SalesAgent, on_delete=models.CASCADE)
    branch = models.ForeignKey(
        Branch, on_delete=models.CASCADE, related_name="agent_reports", default=1
    )
    report_date = models.DateField()
    reporting_period = models.CharField(max_length=20)
    policies_sold = models.IntegerField(default=0)
    total_premium = models.DecimalField(max_digits=12, decimal_places=2)
    commission_earned = models.DecimalField(max_digits=10, decimal_places=2)
    target_achievement = models.DecimalField(max_digits=5, decimal_places=2)
    renewal_rate = models.DecimalField(max_digits=5, decimal_places=2)
    customer_retention = models.DecimalField(max_digits=5, decimal_places=2)

    def __str__(self):
        return f"Report for {self.agent} on {self.report_date}"

    class Meta:
        verbose_name = "Agent Report"
        verbose_name_plural = "Agent Reports"


class Loan(models.Model):
    policy_holder = models.ForeignKey(
        "PolicyHolder", on_delete=models.CASCADE, related_name="loans"
    )
    loan_amount = models.DecimalField(
        max_digits=12, decimal_places=2, help_text="Principal loan amount."
    )
    interest_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=10.00,
        help_text="Annual interest rate in percentage.",
    )
    remaining_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        editable=False,
        help_text="Remaining loan principal balance.",
    )
    accrued_interest = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        editable=False,
        help_text="Interest accrued on the loan.",
    )
    loan_status = models.CharField(
        max_length=50,
        choices=[("Active", "Active"), ("Paid", "Paid")],
        default="Active",
    )
    last_interest_date = models.DateField(
        auto_now_add=True, help_text="Date when interest was last accrued."
    )
    created_at = models.DateField(auto_now_add=True)
    updated_at = models.DateField(auto_now_add=True)

    def calculate_max_loan(
        self, requested_amount: Decimal = None
    ) -> Dict[str, Union[bool, str, Decimal]]:
        """
        Calculate maximum loan amount and validate requested amount if provided.
        """
        try:
            premium_payment = self.policy_holder.premium_payments.first()
            if not premium_payment:
                return {
                    "is_valid": False,
                    "message": "No premium payments found for policy holder",
                    "max_allowed": Decimal("0"),
                    "gsv_value": Decimal("0"),
                }

            gsv = premium_payment.gsv_value
            max_loan = gsv * Decimal("0.90")

            result = {
                "is_valid": True,
                "message": "Maximum loan amount calculated",
                "max_allowed": max_loan,
                "gsv_value": gsv,
            }

            if requested_amount is not None:
                if requested_amount <= Decimal("0"):
                    result.update(
                        {
                            "is_valid": False,
                            "message": "Loan amount must be greater than 0",
                            "requested_amount": requested_amount,
                        }
                    )
                elif requested_amount > max_loan:
                    result.update(
                        {
                            "is_valid": False,
                            "message": f"Loan amount exceeds maximum allowed amount of {max_loan}",
                            "requested_amount": requested_amount,
                        }
                    )
                else:
                    result.update(
                        {
                            "message": "Loan amount is valid",
                            "requested_amount": requested_amount,
                        }
                    )

            return result

        except Exception as e:
            return {
                "is_valid": False,
                "message": f"Error calculating maximum loan: {str(e)}",
                "max_allowed": Decimal("0"),
                "gsv_value": Decimal("0"),
            }

    def clean(self):
        """Validate the loan before saving."""
        if not self.pk:  # Only validate on creation
            validation = self.calculate_max_loan(self.loan_amount)
            if not validation["is_valid"]:
                raise ValidationError({"loan_amount": validation["message"]})

    def save(self, *args, **kwargs):
        """Save the loan with validation."""
        try:
            self.full_clean()  # This will call our clean() method
            if not self.pk:  # On loan creation
                self.remaining_balance = self.loan_amount
            super().save(*args, **kwargs)
        except ValidationError as e:
            raise ValidationError(e.message_dict)
        except Exception as e:
            raise ValidationError(
                {"non_field_errors": [f"Error saving loan: {str(e)}"]}
            )

    def accrue_interest(self):
        """Accrue interest on the remaining balance."""
        if self.loan_status != "Active":
            return

        today = date.today()
        days_since_last_accrual = (today - self.last_interest_date).days

        if days_since_last_accrual <= 0:
            return

        try:
            daily_rate = self.interest_rate / 100 / 365
            interest = (
                self.remaining_balance
                * Decimal(daily_rate)
                * Decimal(days_since_last_accrual)
            )

            self.accrued_interest += interest.quantize(Decimal("1.00"))
            self.last_interest_date = today
            self.save()
        except Exception as e:
            raise ValidationError(f"Error accruing interest: {str(e)}")

    def __str__(self):
        return f"Loan for {self.policy_holder} - {self.loan_status}"


# Loan Repayment Model
class LoanRepayment(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="repayments")
    repayment_date = models.DateField(auto_now_add=True)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, help_text="Amount paid towards the loan."
    )
    repayment_type = models.CharField(
        max_length=50,
        choices=[
            ("Principal", "Principal"),
            ("Interest", "Interest"),
            ("Both", "Both"),
        ],
        default="Both",
    )
    remaining_loan_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        editable=False,
        help_text="Remaining loan balance after this repayment.",
    )

    def process_repayment(self):
        """Apply repayment to interest and/or principal."""
        remaining = self.amount

        if self.repayment_type in ("Both", "Interest"):
            # Deduct from accrued interest first
            interest_payment = min(remaining, self.loan.accrued_interest)
            self.loan.accrued_interest -= interest_payment
            remaining -= interest_payment

        if self.repayment_type in ("Both", "Principal") and remaining > 0:
            # Deduct from remaining balance
            principal_payment = min(remaining, self.loan.remaining_balance)
            self.loan.remaining_balance -= principal_payment

        # Update loan status
        if self.loan.remaining_balance <= 0 and self.loan.accrued_interest <= 0:
            self.loan.loan_status = "Paid"

        # Save the updated loan
        self.loan.save()

        # Set the remaining loan balance for this repayment
        self.remaining_loan_balance = (
            self.loan.remaining_balance + self.loan.accrued_interest
        )

    def save(self, *args, **kwargs):
        """Process repayment before saving."""
        self.process_repayment()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Repayment for {self.loan} on {self.repayment_date}"

