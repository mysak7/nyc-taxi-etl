# Step Functions dělá to, co v Airflow DAGu dynamic task mapping: kolik je práce, se ví
# až za běhu. Airflow ani Step Functions nejsou dvě verze pipeline -- obojí jen jinak
# spouští týž kontejner, takže platí, co je v DAGu: business logika je v aplikaci.
#
# Standard, ne Express: Express je at-least-once a končí po 5 minutách. Cena je tady
# nerozhodná (30 běhů * ~12 přechodů = 0,01 USD/měsíc), spolehlivost ne.

resource "aws_cloudwatch_log_group" "states" {
  name              = "/aws/vendedlogs/states/${local.name}"
  retention_in_days = 30
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = local.name
  role_arn = aws_iam_role.states.arn
  type     = "STANDARD"

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.states.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = jsonencode({
    Comment = "detect -> process(N mesicu) -> check-freshness"
    StartAt = "Detect"
    States = {
      # JsonMerge, aby vstup exekuce mohl být `{}` i `{"from":"2024-01","force":true}`
      # -- backfill je týž stroj s jiným vstupem, ne druhá pipeline.
      Detect = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.app.arn
          "Payload.$"  = "States.JsonMerge($, States.StringToJson('{\"command\": \"detect\"}'), false)"
        }
        OutputPath = "$.Payload"
        Retry      = local.retry_transient
        Next       = "HasWork"
      }

      # Většina běhů nemá co dělat (šest HEADů, žádná změna ETagu). Prázdný seznam
      # nemá chodit do Map: exekuce skončí zeleně za pár sekund.
      HasWork = {
        Type    = "Choice"
        Choices = [{ Variable = "$.months[0]", IsPresent = true, Next = "ProcessMonths" }]
        Default = "CheckFreshness"
      }

      ProcessMonths = {
        Type           = "Map"
        ItemsPath      = "$.months"
        MaxConcurrency = 2 # zdroj je CloudFront, ne náš server, ale slušnost stojí nula
        ItemSelector = {
          command   = "run"
          "year.$"  = "$$.Map.Item.Value.year"
          "month.$" = "$$.Map.Item.Value.month"
          "etag.$"  = "$$.Map.Item.Value.etag"
        }
        ItemProcessor = {
          ProcessorConfig = { Mode = "INLINE" }
          StartAt         = "RunMonth"
          States = {
            RunMonth = {
              Type       = "Task"
              Resource   = "arn:aws:states:::lambda:invoke"
              Parameters = { FunctionName = aws_lambda_function.app.arn, "Payload.$" = "$" }
              ResultSelector = {
                "rows.$"   = "$.Payload.rows"
                "run_id.$" = "$.Payload.run_id"
                "timing.$" = "$.Payload.timing"
              }
              ResultPath = "$.result"
              Retry      = local.retry_transient
              # Selhaný březen neshodí leden: chyba se posbírá a vyhodnotí až na konci.
              # Bez Catch by Map zrušil i měsíce, které ještě čekají ve frontě.
              Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "MonthFailed" }]
              Next  = "MonthOk"
            }
            MonthOk = {
              Type = "Pass"
              Parameters = {
                failed     = false
                "year.$"   = "$.year"
                "month.$"  = "$.month"
                "result.$" = "$.result"
              }
              End = true
            }
            MonthFailed = {
              Type = "Pass"
              Parameters = {
                failed    = true
                "year.$"  = "$.year"
                "month.$" = "$.month"
                "error.$" = "$.error.Error"
                "cause.$" = "$.error.Cause"
              }
              End = true
            }
          }
        }
        ResultSelector = { "failed.$" = "$[*].failed", "months.$" = "$" }
        ResultPath     = "$.processed"
        Next           = "Summarize"
      }

      Summarize = {
        Type       = "Pass"
        Parameters = { "any_failed.$" = "States.ArrayContains($.processed.failed, true)" }
        ResultPath = "$.summary"
        Next       = "CheckFreshness"
      }

      # Běží i ve dnech bez práce -- právě tehdy je zelená exekuce nejvíc podezřelá.
      # Selhání se nechytá: publikovaný měsíc bez výstupu má exekuci shodit.
      CheckFreshness = {
        Type           = "Task"
        Resource       = "arn:aws:states:::lambda:invoke"
        Parameters     = { FunctionName = aws_lambda_function.app.arn, Payload = { command = "check-freshness" } }
        ResultSelector = { "state.$" = "$.Payload" }
        ResultPath     = "$.freshness"
        Retry          = local.retry_transient
        Next           = "AnyMonthFailed"
      }

      # Zelená exekuce nesmí lhát: freshness sama o sobě nestačí. Měsíc, kterému se
      # změnil ETag a přepočet spadl, má na S3 pořád starý výstup -- žádná mezera,
      # a přesto se ta práce neudělala.
      AnyMonthFailed = {
        Type = "Choice"
        Choices = [
          { Variable = "$.summary", IsPresent = false, Next = "Succeeded" },
          { Variable = "$.summary.any_failed", BooleanEquals = true, Next = "MonthsFailed" },
        ]
        Default = "Succeeded"
      }

      MonthsFailed = {
        Type  = "Fail"
        Error = "MonthsFailed"
        Cause = "Nejmene jeden mesic skoncil chybou; detail je v $.processed.months."
      }

      Succeeded = { Type = "Succeed" }
    }
  })
}

locals {
  # Opakovat má smysl jen přechodné chyby. PermanentError (měsíc není publikovaný) ani
  # DataQualityError (překročený práh) se opakováním nespraví -- retry stáhne totéž
  # a spadne stejně. Default "retries=3 na všechno" znamená, že nepublikovaný měsíc
  # umírá 45 minut a rozbitá data se počítají čtyřikrát.
  retry_transient = [
    {
      ErrorEquals     = ["TransientError"]
      IntervalSeconds = 120
      MaxAttempts     = 2
      BackoffRate     = 2.0
    },
    {
      # Chyby samotné Lambdy (throttling, 5xx služby) -- s aplikací nesouvisí.
      ErrorEquals     = ["Lambda.TooManyRequestsException", "Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException"]
      IntervalSeconds = 5
      MaxAttempts     = 3
      BackoffRate     = 2.0
    },
  ]
}

# ---------------------------------------------------------------------------
# Spouštění: denně. Zdroj publikuje s lagem 26-85 dní, takže na hodině nezáleží.
# ---------------------------------------------------------------------------

resource "aws_scheduler_schedule" "daily" {
  name       = local.name
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = "UTC"

  target {
    arn      = aws_sfn_state_machine.pipeline.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({}) # prázdný vstup = okno posledních 6 měsíců podle configu

    retry_policy {
      # Neopakuje pipeline, jen volání StartExecution. Když se nepovede ani to,
      # zítřejší běh tentýž měsíc stejně najde -- práci řídí ETag, ne kalendář.
      maximum_retry_attempts = 3
    }
  }
}

# ---------------------------------------------------------------------------
# Alert až po vyčerpání retries, jedním kanálem. ExecutionsFailed vzniká až když
# Step Functions dojdou pokusy, takže se sem nedostane přechodný 5xx z CloudFrontu.
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudwatch.amazonaws.com" }
      Action    = "SNS:Publish"
      Resource  = aws_sns_topic.alerts.arn
      Condition = { ArnLike = { "aws:SourceArn" = "arn:aws:cloudwatch:${var.region}:${data.aws_caller_identity.current.account_id}:alarm:*" } }
    }]
  })
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "failed" {
  alarm_name          = "${local.name}-execution-failed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.pipeline.arn }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_description   = "Pipeline skoncila chybou po vycerpani retries."
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Druhé tvrzení: scheduler přestal spouštět (smazaný schedule, odebraná práva).
# Bez tohohle alarmu vypadá "nic neběží" úplně stejně jako "všechno je v pořádku".
resource "aws_cloudwatch_metric_alarm" "not_running" {
  alarm_name          = "${local.name}-no-executions"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsStarted"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.pipeline.arn }
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_description   = "Za dva dny nezacala zadna exekuce -- neběží scheduler."
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
